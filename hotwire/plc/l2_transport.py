"""
Abstract Layer-2 transport for HomePlug management frames.

The SLAC state machine needs to send/receive HomePlug MME frames. On
real hardware that means ``pcap`` on a pypcap-opened ethernet interface;
in the test harness that means an in-memory queue pair between two
peer ``HomePlug`` instances.

Both implementations fulfil the :class:`L2Transport` protocol, so the
SLAC state machine can be written once and tested against the mock
before we ever plug in a real modem.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Optional, Protocol


class L2Transport(Protocol):
    """Minimal Layer-2 send/receive contract."""

    def send(self, frame: bytes) -> None:
        """Transmit one raw ethernet frame. Non-blocking. No MTU check
        — caller is responsible for staying under the MTU (1500 bytes
        for HomePlug AV)."""
        ...

    def recv(self) -> Optional[bytes]:
        """Return the next received ethernet frame, or None if the
        receive queue is empty. Non-blocking."""
        ...

    def close(self) -> None:
        ...


class PipeL2Transport:
    """Pure-Python L2 transport — two instances share a pair of queues
    so everything one ``send``s appears in the peer's ``recv``.

    Used by the test harness to run two ``HomePlug`` peers back-to-back
    without a real PLC modem or pcap. Thread-safe; either side can be
    driven by its own worker thread.

    Usage::

        evse_tx, pev_tx = PipeL2Transport.pair()
        evse_hp = HomePlug(evse_tx, ...)
        pev_hp  = HomePlug(pev_tx, ...)
    """

    def __init__(self, tx_queue: "deque[bytes]", rx_queue: "deque[bytes]",
                 lock: threading.Lock) -> None:
        self._tx = tx_queue
        self._rx = rx_queue
        self._lock = lock
        self._closed = False

    @classmethod
    def pair(cls) -> tuple["PipeL2Transport", "PipeL2Transport"]:
        """Create two connected endpoints. Frames sent on A arrive on B's
        receive queue and vice-versa."""
        q_a_to_b: "deque[bytes]" = deque()
        q_b_to_a: "deque[bytes]" = deque()
        lock = threading.Lock()
        a = cls(tx_queue=q_a_to_b, rx_queue=q_b_to_a, lock=lock)
        b = cls(tx_queue=q_b_to_a, rx_queue=q_a_to_b, lock=lock)
        return a, b

    def send(self, frame: bytes) -> None:
        if self._closed:
            return
        with self._lock:
            self._tx.append(bytes(frame))

    def recv(self) -> Optional[bytes]:
        if self._closed:
            return None
        with self._lock:
            if not self._rx:
                return None
            return self._rx.popleft()

    def close(self) -> None:
        self._closed = True
        with self._lock:
            self._tx.clear()
            self._rx.clear()


class PcapL2Transport:
    """Real pcap-backed L2 transport. Opens the interface once in
    promiscuous + immediate mode; non-blocking ``recv`` polls pcap with
    a short timeout.

    Raises :class:`RuntimeError` at construction time when pypcap or the
    interface isn't available. Caller should catch and fall back to
    :class:`PipeL2Transport` (paired with another in-process peer) when
    real hardware isn't present.
    """

    def __init__(self, interface: str, snaplen: int = 1600) -> None:
        try:
            import pcap                                        # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "pypcap not importable. Install `pcap-ct` "
                "(note: not plain `pypcap`)."
            ) from e
        try:
            self._sock = pcap.pcap(
                name=interface,
                snaplen=snaplen,
                promisc=True,
                immediate=True,
                timeout_ms=50,
            )
            # Filter: only HomePlug AV ethertype.
            self._sock.setfilter("ether proto 0x88E1")
        except Exception as e:                                  # noqa: BLE001
            raise RuntimeError(
                f"pcap.open({interface!r}) failed: {e}"
            ) from e
        self._interface = interface

    def send(self, frame: bytes) -> None:
        try:
            self._sock.sendpacket(frame)
        except Exception:                                       # noqa: BLE001
            # On a real modem, occasional sendpacket drops are recoverable
            # via retransmit at the SLAC state-machine layer.
            pass

    def recv(self) -> Optional[bytes]:
        # pypcap dispatch-based receive loop. We run one non-blocking
        # step per call — if there's a packet, return it; otherwise None.
        result: list[bytes] = []

        def _cb(ts, pkt):
            result.append(bytes(pkt))

        try:
            self._sock.dispatch(1, _cb)        # fetch at most 1 packet
        except Exception:                                       # noqa: BLE001
            return None
        return result[0] if result else None

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:                                       # noqa: BLE001
            pass
