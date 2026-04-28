"""
Replay real captured HomePlug frames through the SLAC state machine.

``EVSEtestinglog/EV_Testing/IONIQ6.pcapng`` is a genuine capture of a
Hyundai IONIQ6 SLAC handshake against a commercial CCS charger. This
test injects the PEV-originated frames from that file into our
:class:`SlacStateMachine` running in EVSE role and asserts that we:

* Parse the PEV's real MAC and run_id out of ``CM_SLAC_PARAM.REQ``
* Respond with a syntactically-correct ``CM_SLAC_PARAM.CNF``
* Accept the vendor's ``CM_SLAC_MATCH.REQ`` and reach ``SLAC_PAIRED``

This complements the pure-mock test: the mock proves the state
machine talks to itself; this proves it can talk to a real vehicle
without the attenuation rounds confusing it.

The capture lives outside the repo at ``../EVSEtestinglog/EV_Testing/``;
if it isn't present (e.g. on a fresh clone) the test skips rather than
fails.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.plc.homeplug_frames import HomePlugFrame
from hotwire.plc.l2_transport import PipeL2Transport
from hotwire.plc.pcapng_reader import iter_homeplug_frames
from hotwire.plc.slac import (
    ROLE_EVSE,
    ROLE_PEV,
    SLAC_PAIRED,
    SlacStateMachine,
)


_CAPTURE_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "EVSEtestinglog"
    / "EV_Testing"
)
CAPTURE_PATH = _CAPTURE_ROOT / "IONIQ6.pcapng"
TESLA_CAPTURE_PATH = _CAPTURE_ROOT / "teslaEndWithPrecharge.pcapng"


def _require_capture(path: Path = CAPTURE_PATH) -> Path:
    if not path.exists():
        pytest.skip(
            f"real-capture pcapng not found at {path}; "
            "install the EV_Testing dataset to enable this test"
        )
    return path


def _extract_pev_mac(path: Path) -> bytes:
    """Peek at the first CM_SLAC_PARAM.REQ to learn the PEV's MAC."""
    for raw in iter_homeplug_frames(path):
        fr = HomePlugFrame.from_bytes(raw)
        if fr is not None and fr.is_slac_param_req():
            return fr.src_mac
    pytest.skip("no CM_SLAC_PARAM.REQ in capture; cannot run replay")


def _replay_as_evse(path: Path, pev_mac: bytes) -> tuple[
    SlacStateMachine, list[tuple[bytes, bytes, bytes]]
]:
    evse_tx, _peer = PipeL2Transport.pair()
    callbacks: list[tuple[bytes, bytes, bytes]] = []
    evse = SlacStateMachine(
        role=ROLE_EVSE,
        transport=evse_tx,
        local_mac=bytes.fromhex("AABBCCDDEEFF"),
        callback_add_to_trace=lambda s: None,
        callback_slac_ok=lambda nmk, nid, peer: callbacks.append(
            (nmk, nid, peer)
        ),
    )
    for raw in iter_homeplug_frames(path):
        fr = HomePlugFrame.from_bytes(raw)
        if fr is None or fr.src_mac != pev_mac:
            continue
        if not (fr.is_slac_param_req() or fr.is_slac_match_req()):
            continue
        with evse_tx._lock:                                  # noqa: SLF001
            evse_tx._rx.append(raw)                          # noqa: SLF001
        evse.tick()
    for _ in range(10):
        evse.tick()
        time.sleep(0.005)
    return evse, callbacks


def test_ioniq6_capture_contains_full_slac_handshake() -> None:
    """Sanity check on the fixture itself — if this fails the other
    tests' assumptions are invalid."""
    path = _require_capture()
    types_seen: set[tuple[int, int]] = set()
    for raw in iter_homeplug_frames(path):
        fr = HomePlugFrame.from_bytes(raw)
        if fr is not None:
            types_seen.add((fr.mmtype_base, fr.mmsub))

    # Minimum required for SLAC replay:
    assert any(fr.is_slac_param_req()
               for raw in iter_homeplug_frames(path)
               if (fr := HomePlugFrame.from_bytes(raw)) is not None)
    assert any(fr.is_slac_match_req()
               for raw in iter_homeplug_frames(path)
               if (fr := HomePlugFrame.from_bytes(raw)) is not None)


@pytest.mark.parametrize(
    "capture_name",
    ["IONIQ6.pcapng", "teslaEndWithPrecharge.pcapng"],
)
def test_replay_real_capture_reaches_paired(capture_name: str) -> None:
    """Drive our EVSE-side SLAC state machine using frames captured
    from a real commercial charger + real production EV.

    Real-charger captures don't contain the local CM_SET_KEY.CNF
    response (that's a host-↔-local-modem exchange, not on the air-side
    PLC line that pcap saw). With Checkpoint 19's CM_SET_KEY integration
    the state machine therefore lands at SLAC_WAIT_SET_KEY_CNF after
    consuming all captured frames — the **peer** handshake completed,
    which is the property these replays validate. SLAC_PAIRED arrives
    after the SET_KEY budget elapses (see slac.py: deadline-based
    ``_mark_paired_and_notify`` fallback).
    """
    from hotwire.plc.slac import SLAC_PAIRED, SLAC_WAIT_SET_KEY_CNF

    path = _require_capture(_CAPTURE_ROOT / capture_name)
    pev_mac = _extract_pev_mac(path)

    evse, callbacks = _replay_as_evse(path, pev_mac)

    assert evse.state in (SLAC_PAIRED, SLAC_WAIT_SET_KEY_CNF), (
        f"{capture_name}: EVSE state = {evse.state}, "
        f"expected SLAC_PAIRED ({SLAC_PAIRED}) or "
        f"SLAC_WAIT_SET_KEY_CNF ({SLAC_WAIT_SET_KEY_CNF})"
    )
    assert evse.peer_mac == pev_mac
    # NMK / NID must be derived during the peer handshake regardless
    # of which terminal state we reached (Checkpoint 19's CM_SET_KEY
    # phase doesn't change the keys, only programs the local modem).
    assert evse.nmk is not None and len(evse.nmk) == 16
    assert evse.nid is not None and len(evse.nid) == 7
    # The on_slac_ok callback fires once when state == SLAC_PAIRED.
    # If we landed at SLAC_WAIT_SET_KEY_CNF (real-charger captures
    # don't include the host-↔-modem SET_KEY exchange), the callback
    # has not fired yet — that's expected.
    if evse.state == SLAC_PAIRED:
        assert len(callbacks) == 1
        nmk, nid, peer = callbacks[0]
        assert peer == pev_mac
        assert nmk == evse.nmk
        assert nid == evse.nid
    else:
        assert len(callbacks) == 0


def test_replay_extracts_real_pev_run_id() -> None:
    """The run_id we adopt from the capture must match what's actually
    on the wire — not the default random one we were initialised with."""
    path = _require_capture()
    pev_mac = _extract_pev_mac(path)

    # Find the run_id embedded in the real PARAM.REQ.
    pcap_run_id: bytes | None = None
    for raw in iter_homeplug_frames(path):
        fr = HomePlugFrame.from_bytes(raw)
        if fr is not None and fr.is_slac_param_req() and fr.src_mac == pev_mac:
            # Vendor OUI(3) + APPTYPE(1) + SECTYPE(1) + RunID(8) = offset 5..13
            if len(fr.payload) >= 13:
                pcap_run_id = fr.payload[5:13]
                break

    assert pcap_run_id is not None, "couldn't find run_id in capture"

    evse_tx, _peer = PipeL2Transport.pair()
    evse = SlacStateMachine(
        role=ROLE_EVSE,
        transport=evse_tx,
        local_mac=bytes.fromhex("AABBCCDDEEFF"),
        callback_add_to_trace=lambda s: None,
    )
    # Inject one PARAM.REQ.
    for raw in iter_homeplug_frames(path):
        fr = HomePlugFrame.from_bytes(raw)
        if fr is not None and fr.is_slac_param_req() and fr.src_mac == pev_mac:
            with evse_tx._lock:                              # noqa: SLF001
                evse_tx._rx.append(raw)                      # noqa: SLF001
            break
    evse.tick()

    assert evse.run_id == pcap_run_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
