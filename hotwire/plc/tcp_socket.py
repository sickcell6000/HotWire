"""
IPv6 TCP client / server sockets for V2G protocol transport.

Supports link-local fe80:: addresses with scope IDs for real hardware, and
loopback ``::1`` for pure-software simulation mode where two processes on
the same host talk directly over TCP.

Adapted from pyPLC's pyPlcTcpSocket.py (GPL-3.0, uhi22).
"""
from __future__ import annotations

import errno
import os
import select
import socket
import time
from typing import Callable

from ..core.config import getConfigValue, getConfigValueBool

# State notification codes passed to the FSM via ``callbackStateNotification``.
STATE_DISCONNECTED = 0
STATE_LISTENING = 1
STATE_CONNECTED = 2


def _resolve_tcp_port() -> int:
    """Return the TCP port for V2GTP.

    DIN 70121 §8.7.2 does NOT mandate a specific port; both peers must
    pick one in the IANA dynamic range (49152-65535) and advertise it
    via SDP. The common convention is port 15118 (matching the ISO name)
    but it is purely an operator choice.

    Config keys (tried in order, for backwards compatibility):

      * ``tcp_port_use_well_known`` (bool, preferred) — force port 15118
        if True, else fall back to ``tcp_port_alternative``.
      * ``tcp_port_15118_compliant`` (deprecated) — same semantics,
        misleading name; kept as alias.
      * ``tcp_port_alternative`` (int) — explicit port number. Default
        57122 (chosen to sit in the dynamic range and avoid clashing
        with any real DIN 70121 charging station a tester might have
        running in the same LAN).
    """
    for key in ("tcp_port_use_well_known", "tcp_port_15118_compliant"):
        try:
            if getConfigValueBool(key):
                return 15118
        except SystemExit:
            # Config key wasn't found — try the next one. We suppress
            # SystemExit because getConfigValueBool calls sys.exit() on
            # missing keys (legacy pyPLC behaviour).
            continue
    try:
        return int(getConfigValue("tcp_port_alternative"))
    except (ValueError, KeyError, SystemExit):
        return 57122


class pyPlcTcpClientSocket:
    """IPv6 TCP client used in PEV mode to connect to the EVSE."""

    def __init__(self, callbackAddToTrace: Callable[[str], None]) -> None:
        self.callbackAddToTrace = callbackAddToTrace
        self.sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.isConnected = False
        self.rxData: bytes = b""

    def addToTrace(self, s: str) -> None:
        self.callbackAddToTrace(s)

    def connect(self, host: str, port: int) -> None:
        """Connect to ``host`` (IPv6 literal, loopback, or link-local) on ``port``."""
        socket_addr_tuple = (host, port, 0, 0)
        try:
            self.addToTrace(f"TCP connecting to {host} port {port}...")
            self.sock.settimeout(0.5)

            # Handle link-local scope identifiers: either numeric %N (Windows)
            # or interface name %eth0 (Linux/macOS).
            if host.lower().startswith("fe80::"):
                if "%" in host:
                    actual_host, scope_part = host.split("%", 1)
                    if scope_part.isdigit():
                        socket_addr_tuple = (actual_host, port, 0, int(scope_part))
                    elif os.name != "nt":
                        try:
                            scope_id_val = socket.if_nametoindex(scope_part)
                            socket_addr_tuple = (actual_host, port, 0, scope_id_val)
                        except OSError:
                            socket_addr_tuple = (actual_host, port, 0, 0)
                    else:
                        addr_info_list = socket.getaddrinfo(
                            host, port, socket.AF_INET6, socket.SOCK_STREAM
                        )
                        if addr_info_list:
                            socket_addr_tuple = addr_info_list[0][4]
                elif os.name != "nt":
                    # Try to auto-scope with the configured interface
                    try:
                        ethInterface = getConfigValue("eth_interface")
                        addr_info_list = socket.getaddrinfo(
                            f"{host}%{ethInterface}", port,
                            socket.AF_INET6, socket.SOCK_STREAM,
                        )
                    except Exception:
                        addr_info_list = socket.getaddrinfo(
                            host, port, socket.AF_INET6, socket.SOCK_STREAM
                        )
                    if addr_info_list:
                        socket_addr_tuple = addr_info_list[0][4]
            else:
                # Global or loopback address — let getaddrinfo handle it.
                addr_info_list = socket.getaddrinfo(
                    host, port, socket.AF_INET6, socket.SOCK_STREAM
                )
                if addr_info_list:
                    socket_addr_tuple = addr_info_list[0][4]

            self.addToTrace(f"Connecting with sockaddr: {socket_addr_tuple}")
            self.sock.connect(socket_addr_tuple)
            self.sock.setblocking(False)
            self.isConnected = True
            self.addToTrace(f"TCP connected to {socket_addr_tuple[0]}:{socket_addr_tuple[1]}")
        except socket.timeout:
            self.addToTrace(f"TCP connection timed out: {host}:{port}")
            self.isConnected = False
        except OSError as e:
            self.addToTrace(f"TCP connection failed: {e}")
            self.isConnected = False

    def disconnect(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.isConnected = False
        self.addToTrace("TCP disconnected and reset.")

    def transmit(self, msg: bytes | bytearray) -> int:
        if not self.isConnected:
            return -1
        totalsent = 0
        MSGLEN = len(msg)
        try:
            while totalsent < MSGLEN:
                sent = self.sock.send(msg[totalsent:])
                if sent == 0:
                    self.isConnected = False
                    self.addToTrace("TCP socket connection broken (send returned 0)")
                    self.disconnect()
                    return -1
                totalsent += sent
        except OSError as e:
            self.isConnected = False
            self.addToTrace(f"TCP send error: {e}")
            self.disconnect()
            return -1
        return 0

    def isRxDataAvailable(self) -> bool:
        if not self.isConnected:
            return False
        try:
            msg = self.sock.recv(4096)
            if len(msg) == 0:
                self.addToTrace("TCP connection gracefully closed by server.")
                self.isConnected = False
                self.disconnect()
                return False
            self.rxData = msg
            return True
        except OSError as e:
            err = e.args[0] if e.args else 0
            if err in (errno.EAGAIN, errno.EWOULDBLOCK):
                return False
            self.addToTrace(f"TCP receive error: {e}")
            self.isConnected = False
            self.disconnect()
            return False

    def getRxData(self) -> bytes:
        d = self.rxData
        self.rxData = b""
        return d


class pyPlcTcpServerSocket:
    """IPv6 TCP server used in EVSE mode to accept PEV connections."""

    BUFFER_SIZE = 1024

    def __init__(
        self,
        callbackAddToTrace: Callable[[str], None],
        callbackStateNotification: Callable[[int], None],
        ip_address_to_bind: str = "",
    ) -> None:
        self.callbackAddToTrace = callbackAddToTrace
        self.callbackStateNotification = callbackStateNotification
        self.ipAdress = ip_address_to_bind
        self.tcpPort = _resolve_tcp_port()
        self.ourSocket: socket.socket | None = None
        self.read_list: list[socket.socket] = []
        self.client_sockets: dict[socket.socket, tuple] = {}
        self.rxData: bytes = b""
        self.setup_socket()

    def addToTrace(self, s: str) -> None:
        self.callbackAddToTrace(s)

    def setup_socket(self) -> None:
        try:
            if self.ourSocket:
                self.ourSocket.close()
        except OSError as e:
            self.addToTrace(f"Error closing old socket: {e}")

        self.ourSocket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM, 0)
        self.ourSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        bind_address_ip_part = self.ipAdress
        scope_id = 0
        if self.ipAdress.lower().startswith("fe80::") and "%" in self.ipAdress:
            parts = self.ipAdress.split("%", 1)
            bind_address_ip_part = parts[0]
            scope_identifier = parts[1]
            if scope_identifier.isdigit():
                scope_id = int(scope_identifier)
            elif os.name != "nt":
                try:
                    scope_id = socket.if_nametoindex(scope_identifier)
                except OSError:
                    scope_id = 0

        try:
            self.addToTrace(
                f"Binding TCP server to '{bind_address_ip_part}' port {self.tcpPort} scope {scope_id}"
            )
            self.ourSocket.bind((bind_address_ip_part, self.tcpPort, 0, scope_id))
            self.ourSocket.listen(5)
            self.read_list = [self.ourSocket]
            self.client_sockets = {}
            self.callbackStateNotification(STATE_LISTENING)
            self.addToTrace(
                f"TCP server listening on {bind_address_ip_part}:{self.tcpPort}"
            )
        except OSError as e:
            self.addToTrace(f"TCP bind/listen failed: {e}")
            self.ourSocket = None
            self.read_list = []

    def resetTheConnection(self) -> None:
        self.addToTrace("Resetting TCP socket...")
        for client_sock in list(self.client_sockets.keys()):
            try:
                client_sock.close()
            except OSError:
                pass
            if client_sock in self.read_list:
                self.read_list.remove(client_sock)
        self.client_sockets.clear()
        self.setup_socket()

    def isRxDataAvailable(self) -> bool:
        return len(self.rxData) > 0

    def getRxData(self) -> bytes:
        d = self.rxData
        self.rxData = b""
        return d

    def transmit(self, txMessage: bytes | bytearray) -> int:
        if not self.client_sockets:
            self.addToTrace("TCP Transmit failed: No connected clients.")
            return -1
        client_sock = list(self.client_sockets.keys())[-1]
        totalsent = 0
        MSGLEN = len(txMessage)
        try:
            while totalsent < MSGLEN:
                sent = client_sock.send(txMessage[totalsent:])
                if sent == 0:
                    self.addToTrace("socket connection broken (send returned 0)")
                    self.handle_client_disconnection(client_sock)
                    return -1
                totalsent += sent
        except OSError as e:
            self.addToTrace(f"TCP send error: {e}")
            self.handle_client_disconnection(client_sock)
            return -1
        return 0

    def handle_client_disconnection(self, client_socket: socket.socket) -> None:
        client_addr = self.client_sockets.get(client_socket, "Unknown")
        self.addToTrace(f"Client {client_addr} disconnected.")
        if client_socket in self.read_list:
            self.read_list.remove(client_socket)
        if client_socket in self.client_sockets:
            del self.client_sockets[client_socket]
        try:
            client_socket.close()
        except OSError:
            pass
        self.callbackStateNotification(
            STATE_LISTENING if not self.client_sockets else STATE_CONNECTED
        )

    def mainfunction(self) -> None:
        if not self.ourSocket:
            self.addToTrace("Error: TCP listening socket not set up; re-trying.")
            time.sleep(1)
            self.setup_socket()
            return

        timeout_s = 0.05
        if not self.read_list and self.ourSocket:
            self.read_list = [self.ourSocket]

        try:
            readable, _writable, errored = select.select(
                self.read_list, [], [], timeout_s
            )
        except ValueError:
            # A closed socket in the list — clean up and retry next cycle.
            self.read_list = [
                s for s in ([self.ourSocket] if self.ourSocket else [])
                + list(self.client_sockets.keys())
                if hasattr(s, "fileno") and s.fileno() != -1
            ]
            return

        for s in readable:
            if s is self.ourSocket:
                try:
                    client_socket, address = self.ourSocket.accept()
                    client_socket.setblocking(False)
                    self.read_list.append(client_socket)
                    self.client_sockets[client_socket] = address
                    self.addToTrace(f"Connection from {address}")
                    self.callbackStateNotification(STATE_CONNECTED)
                except OSError as e:
                    self.addToTrace(f"Error accepting TCP connection: {e}")
            else:
                try:
                    data = s.recv(self.BUFFER_SIZE)
                    if data:
                        self.rxData = data
                    else:
                        self.handle_client_disconnection(s)
                except OSError as e:
                    err = e.args[0] if e.args else 0
                    if err in (errno.ECONNRESET, errno.ENOTCONN, errno.ESHUTDOWN):
                        self.handle_client_disconnection(s)
                    elif err in (errno.EAGAIN, errno.EWOULDBLOCK):
                        pass
                    else:
                        self.addToTrace(f"TCP receive error: {e}")
                        self.handle_client_disconnection(s)

        for s in errored:
            self.addToTrace(f"Socket error on {self.client_sockets.get(s, s)}")
            self.handle_client_disconnection(s)
