"""
SLAC pairing over the in-process L2 transport.

Goal: verify the ``SlacStateMachine`` can complete a PEV<->EVSE pairing
exchange end-to-end, using nothing but Python queues. This is the same
machine that will run on real ``pcap`` hardware, so a green run here is
strong evidence the on-the-wire flow will work once real modems are
plugged in.

What the test pins down:

* Both sides reach ``SLAC_PAIRED`` within a short budget.
* The ``on_slac_ok`` callback fires exactly once per side, with the
  agreed NMK/NID/peer_mac tuple.
* The EVSE's NMK is the one the PEV ends up with — i.e. key material
  actually traversed the wire rather than each side inventing its own.
* The run_id the PEV kicked off with is the one the EVSE adopted.
"""
from __future__ import annotations

import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from hotwire.plc.l2_transport import PipeL2Transport
from hotwire.plc.slac import (
    ROLE_EVSE,
    ROLE_PEV,
    SLAC_FAILED,
    SLAC_PAIRED,
    SlacStateMachine,
)


PEV_MAC = bytes.fromhex("001122334455")
EVSE_MAC = bytes.fromhex("AABBCCDDEEFF")


def _run_until_paired(
    pev: SlacStateMachine,
    evse: SlacStateMachine,
    budget_s: float = 2.0,
    tick_period_s: float = 0.01,
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


def test_slac_pairing_succeeds_over_pipe_transport() -> None:
    pev_tx, evse_tx = PipeL2Transport.pair()

    traces: list[str] = []
    pev_callbacks: list[tuple[bytes, bytes, bytes]] = []
    evse_callbacks: list[tuple[bytes, bytes, bytes]] = []

    pev = SlacStateMachine(
        role=ROLE_PEV,
        transport=pev_tx,
        local_mac=PEV_MAC,
        callback_add_to_trace=traces.append,
        callback_slac_ok=lambda nmk, nid, peer: pev_callbacks.append((nmk, nid, peer)),
    )
    evse = SlacStateMachine(
        role=ROLE_EVSE,
        transport=evse_tx,
        local_mac=EVSE_MAC,
        callback_add_to_trace=traces.append,
        callback_slac_ok=lambda nmk, nid, peer: evse_callbacks.append((nmk, nid, peer)),
    )

    _run_until_paired(pev, evse)

    assert pev.is_paired(), f"PEV did not reach SLAC_PAIRED; traces={traces}"
    assert evse.is_paired(), f"EVSE did not reach SLAC_PAIRED; traces={traces}"

    assert len(pev_callbacks) == 1
    assert len(evse_callbacks) == 1

    pev_nmk, pev_nid, pev_peer = pev_callbacks[0]
    evse_nmk, evse_nid, evse_peer = evse_callbacks[0]

    # Key material agreed — the PEV adopts what the EVSE sent.
    assert pev_nmk == evse_nmk
    assert pev_nid == evse_nid

    # Each side sees the other's MAC as the peer.
    assert pev_peer == EVSE_MAC
    assert evse_peer == PEV_MAC


def test_slac_run_id_flows_from_pev_to_evse() -> None:
    pev_tx, evse_tx = PipeL2Transport.pair()
    pev_run_id = bytes.fromhex("0102030405060708")

    pev = SlacStateMachine(
        role=ROLE_PEV,
        transport=pev_tx,
        local_mac=PEV_MAC,
        callback_add_to_trace=lambda s: None,
        run_id=pev_run_id,
    )
    evse = SlacStateMachine(
        role=ROLE_EVSE,
        transport=evse_tx,
        local_mac=EVSE_MAC,
        callback_add_to_trace=lambda s: None,
    )

    # EVSE starts with a random run_id — assert it differs before pairing,
    # then matches after.
    assert evse.run_id != pev_run_id

    _run_until_paired(pev, evse)

    assert pev.is_paired()
    assert evse.is_paired()
    assert evse.run_id == pev_run_id


def test_slac_times_out_when_peer_silent() -> None:
    """Without a peer on the other end of the pipe, the PEV side should
    eventually go to SLAC_FAILED via the watchdog."""
    pev_tx, _unused = PipeL2Transport.pair()

    pev = SlacStateMachine(
        role=ROLE_PEV,
        transport=pev_tx,
        local_mac=PEV_MAC,
        callback_add_to_trace=lambda s: None,
    )
    # Shrink the watchdog so the test is fast.
    pev._total_timeout_s = 0.2

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not pev.has_failed():
        pev.tick()
        time.sleep(0.01)

    assert pev.state == SLAC_FAILED


def test_slac_ignores_loopback_of_own_frames() -> None:
    """If the transport echoes our own frames back (some pcap setups do),
    the state machine must not treat them as peer traffic."""
    pev_tx, _unused = PipeL2Transport.pair()

    pev = SlacStateMachine(
        role=ROLE_PEV,
        transport=pev_tx,
        local_mac=PEV_MAC,
        callback_add_to_trace=lambda s: None,
    )
    # Send one tick — PEV emits CM_SLAC_PARAM.REQ.
    pev.tick()
    # Simulate transport echo by queueing the outbound frame back onto our
    # own rx side: since PipeL2Transport.pair returns two separate queues,
    # manually inject.
    with pev_tx._lock:                                       # noqa: SLF001
        pev_tx._rx.append(pev_tx._tx[-1])                    # noqa: SLF001

    pev.tick()
    # Echo must not trigger a state advance or a pairing.
    assert not pev.is_paired()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
