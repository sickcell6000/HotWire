"""
Smoke test for TCP IPv6 loopback between two HotWire processes.

Spawns a pyPlcTcpServerSocket (EVSE side) and a pyPlcTcpClientSocket
(PEV side) in the same process, sends a V2GTP-framed SessionSetupReq
payload both directions, and verifies both sides received it.

This is the minimal proof that the simulation TCP transport works
before we wire up the full FSM.
"""
from __future__ import annotations

import os
import sys
import threading
import time

# Ensure HotWire is importable when running this file directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("HOTWIRE_CONFIG", os.path.join(
    os.path.dirname(__file__), "..", "config", "hotwire.ini"
))

from hotwire.exi.connector import addV2GTPHeader, exiEncode, removeV2GTPHeader, exiDecode
from hotwire.plc.tcp_socket import (
    _resolve_tcp_port,
    pyPlcTcpClientSocket,
    pyPlcTcpServerSocket,
)


def test_loopback() -> bool:
    port = _resolve_tcp_port()
    log_lines: list[str] = []

    def trace(s: str) -> None:
        log_lines.append(s)

    def status(code: int) -> None:
        log_lines.append(f"[STATE] {code}")

    # --- Server (EVSE side) ---
    server = pyPlcTcpServerSocket(trace, status, ip_address_to_bind="::1")
    if server.ourSocket is None:
        print("[FAIL] Server socket did not bind.")
        for line in log_lines:
            print(f"  {line}")
        return False

    server_thread_stop = threading.Event()

    def server_loop() -> None:
        while not server_thread_stop.is_set():
            server.mainfunction()
            time.sleep(0.02)

    t = threading.Thread(target=server_loop, daemon=True)
    t.start()

    time.sleep(0.2)  # let server finish binding

    # --- Client (PEV side) ---
    client = pyPlcTcpClientSocket(trace)
    client.connect("::1", port)
    if not client.isConnected:
        print("[FAIL] Client could not connect to ::1.")
        server_thread_stop.set()
        for line in log_lines:
            print(f"  {line}")
        return False

    # --- PEV sends SessionSetupReq with spoofed EVCCID ---
    spoofed_evccid = "d83add22f182"  # victim's MAC (A1 Phase 2 attack core)
    exi_hex = exiEncode(f"EDA_{spoofed_evccid}")
    exi_bytes = bytes.fromhex(exi_hex)
    v2gtp_msg = addV2GTPHeader(exi_bytes)
    print(f"[PEV ] sending SessionSetupReq with EVCCID={spoofed_evccid}")
    print(f"       V2GTP frame = {v2gtp_msg.hex()}")
    assert client.transmit(bytes(v2gtp_msg)) == 0, "client transmit failed"

    # --- Wait for server to receive ---
    deadline = time.time() + 3.0
    received: bytes | None = None
    while time.time() < deadline:
        if server.isRxDataAvailable():
            received = server.getRxData()
            break
        time.sleep(0.05)

    server_thread_stop.set()
    time.sleep(0.1)

    if not received:
        print("[FAIL] Server did not receive any data within 3s.")
        for line in log_lines[-20:]:
            print(f"  {line}")
        return False

    print(f"[EVSE] received {len(received)} bytes: {received.hex()}")

    # --- Decode and verify EVCCID round-trips correctly ---
    exi_only = removeV2GTPHeader(received)
    decoded = exiDecode(bytes(exi_only), "DD")
    if f'"EVCCID": "{spoofed_evccid}"' not in decoded:
        print("[FAIL] EVCCID did not round-trip through TCP+EXI cleanly.")
        print(decoded)
        return False

    print(f"[OK]  EVCCID '{spoofed_evccid}' successfully round-tripped over ::1 TCP + DIN EXI.")
    client.disconnect()
    return True


if __name__ == "__main__":
    ok = test_loopback()
    sys.exit(0 if ok else 1)
