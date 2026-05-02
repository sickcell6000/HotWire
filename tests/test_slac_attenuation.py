"""
Attenuation-round tests for the SLAC state machine.

Two angles:

1. Pure mock over ``PipeL2Transport``: two SlacStateMachines are driven
   in lockstep and we verify the full 6-state sequence (PARAM → sounds
   → ATTEN_CHAR → MATCH) executes end-to-end with no dropped frames.

2. Replay of the full IONIQ6 / Tesla captures: we feed every PEV-origin
   frame — including the 10 MNBC_SOUND and CM_START_ATTEN_CHAR frames
   we previously filtered out — into an EVSE-role state machine and
   assert it pairs. This is the closest we can get to "real hardware"
   without real hardware: the wire bytes come from a production EVSE
   and production vehicle.
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


PEV_MAC = bytes.fromhex("001122334455")
EVSE_MAC = bytes.fromhex("AABBCCDDEEFF")


def _run_until_paired(
    pev: SlacStateMachine,
    evse: SlacStateMachine,
    budget_s: float = 3.0,
    tick_period_s: float = 0.005,
) -> None:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        pev.tick()
        evse.tick()
        if pev.is_paired() and evse.is_paired():
            return
        if pev.has_failed() or evse.has_failed():
            return
        time.sleep(tick_period_s)


def test_attenuation_round_in_pipe_mock() -> None:
    """Both SlacStateMachines drive the full attenuation exchange and
    reach PAIRED without dropping any of the 10 sounds."""
    pev_tx, evse_tx = PipeL2Transport.pair()

    pev_cbs: list[tuple[bytes, bytes, bytes]] = []
    evse_cbs: list[tuple[bytes, bytes, bytes]] = []

    pev = SlacStateMachine(
        role=ROLE_PEV,
        transport=pev_tx,
        local_mac=PEV_MAC,
        callback_add_to_trace=lambda s: None,
        callback_slac_ok=lambda n, i, p: pev_cbs.append((n, i, p)),
    )
    evse = SlacStateMachine(
        role=ROLE_EVSE,
        transport=evse_tx,
        local_mac=EVSE_MAC,
        callback_add_to_trace=lambda s: None,
        callback_slac_ok=lambda n, i, p: evse_cbs.append((n, i, p)),
    )

    _run_until_paired(pev, evse)

    assert pev.is_paired() and evse.is_paired()
    assert len(pev_cbs) == 1 and len(evse_cbs) == 1
    # The EVSE side should have recorded exactly 10 sounds — confirming
    # that the attenuation round actually ran (not just skipped via the
    # compat fast-path).
    assert evse._sounds_received == 10                      # noqa: SLF001
    # PEV should have sent all 10 sounds.
    assert pev._sounds_remaining == 0                       # noqa: SLF001


@pytest.mark.parametrize(
    "capture_name",
    ["IONIQ6.pcapng", "teslaEndWithPrecharge.pcapng"],
)
def test_full_slac_with_attenuation_replay(capture_name: str) -> None:
    """Feed every PEV-origin frame — including the sounds — into an EVSE
    SlacStateMachine and make sure pairing completes.

    This exercises the new SLAC_WAIT_SOUNDS path against real wire data.
    The previous replay test (tests/test_homeplug_slac_replay.py) only
    injected PARAM_REQ and MATCH_REQ; this one injects the full sequence
    a real vehicle produces.
    """
    path = _CAPTURE_ROOT / capture_name
    if not path.exists():
        pytest.skip(f"capture missing: {path}")

    # Discover the PEV's MAC (first PARAM_REQ in the file).
    pev_mac = None
    for raw in iter_homeplug_frames(path):
        fr = HomePlugFrame.from_bytes(raw)
        if fr is not None and fr.is_slac_param_req():
            pev_mac = fr.src_mac
            break
    assert pev_mac is not None, "no PARAM_REQ in capture"

    evse_tx, _peer = PipeL2Transport.pair()
    callbacks: list[tuple[bytes, bytes, bytes]] = []
    evse = SlacStateMachine(
        role=ROLE_EVSE,
        transport=evse_tx,
        local_mac=bytes.fromhex("AABBCCDDEEFF"),
        callback_add_to_trace=lambda s: None,
        callback_slac_ok=lambda n, i, p: callbacks.append((n, i, p)),
    )

    injected = 0
    for raw in iter_homeplug_frames(path):
        fr = HomePlugFrame.from_bytes(raw)
        if fr is None or fr.src_mac != pev_mac:
            continue
        # Everything the PEV can send during SLAC: PARAM_REQ, sounds
        # (START_ATTEN_CHAR + MNBC_SOUND), ATTEN_CHAR.RSP, MATCH_REQ.
        if not (fr.is_slac_param_req()
                or fr.is_start_atten_char_ind()
                or fr.is_mnbc_sound_ind()
                or fr.is_atten_char_rsp()
                or fr.is_slac_match_req()):
            continue
        with evse_tx._lock:                                  # noqa: SLF001
            evse_tx._rx.append(raw)                          # noqa: SLF001
        evse.tick()
        injected += 1

    # Drain any remaining inbound.
    for _ in range(20):
        evse.tick()
        time.sleep(0.002)

    assert injected >= 5, f"expected several frames, got {injected}"
    # Real-charger captures don't include the local CM_SET_KEY.CNF
    # that Checkpoint 19 added — accept either terminal state. See
    # tests/test_homeplug_slac_replay.py for the longer rationale.
    from hotwire.plc.slac import SLAC_WAIT_SET_KEY_CNF
    assert evse.state in (SLAC_PAIRED, SLAC_WAIT_SET_KEY_CNF), (
        f"{capture_name}: EVSE state={evse.state}, "
        f"expected SLAC_PAIRED ({SLAC_PAIRED}) or "
        f"SLAC_WAIT_SET_KEY_CNF ({SLAC_WAIT_SET_KEY_CNF})"
    )
    assert evse.peer_mac == pev_mac


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
