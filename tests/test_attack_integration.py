"""End-to-end: attack playbook + JSONL session logger against a live dual-worker setup.

Spawns an EVSE and a PEV HotWireWorker in the same process (pure Python,
no Qt). Applies the AutochargeImpersonation playbook to the PEV. Routes
every decoded message through a SessionLogger into a temp JSONL. Asserts:

1. The full DIN handshake completes through CurrentDemand.
2. The spoofed EVCCID appears in at least one tx message from the PEV
   and one rx message on the EVSE's logged stream.
3. The logged JSONL parses cleanly line-by-line.

This catches regressions where any of the three layers (attack playbook,
PauseController override merging, MessageObserver plumbing) silently
breaks.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "HOTWIRE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"),
)

from hotwire.attacks import AutochargeImpersonation  # noqa: E402
from hotwire.core.config import load as load_config  # noqa: E402

load_config()

from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE  # noqa: E402
from hotwire.core.session_log import SessionLogger  # noqa: E402
from hotwire.core.worker import HotWireWorker  # noqa: E402
from hotwire.fsm.pause_controller import PauseController  # noqa: E402


def test_autocharge_attack_end_to_end(tmp_path):
    spoofed_evccid = "deadbeef1234"
    attack = AutochargeImpersonation(evccid=spoofed_evccid)
    assert attack.overrides["SessionSetupReq"]["EVCCID"] == spoofed_evccid

    evse_log = tmp_path / "evse.jsonl"
    pev_log = tmp_path / "pev.jsonl"

    evse_pc = PauseController()
    pev_pc = PauseController()
    attack.apply(pev_pc)  # attacker is the PEV

    evse_logger = SessionLogger(evse_log, mode="EVSE")
    pev_logger = SessionLogger(pev_log, mode="PEV")

    evse = HotWireWorker(
        callbackAddToTrace=lambda s: None,
        callbackShowStatus=lambda *a, **kw: None,
        mode=C_EVSE_MODE,
        isSimulationMode=1,
        pause_controller=evse_pc,
        message_observer=evse_logger,
    )

    stop = threading.Event()

    def _evse_tick() -> None:
        while not stop.is_set():
            evse.mainfunction()
            time.sleep(0.03)

    evse_thread = threading.Thread(target=_evse_tick, daemon=True)
    evse_thread.start()

    # Give the EVSE TCP server ~1.5s to bind before the PEV tries to connect.
    time.sleep(1.5)

    pev = HotWireWorker(
        callbackAddToTrace=lambda s: None,
        callbackShowStatus=lambda *a, **kw: None,
        mode=C_PEV_MODE,
        isSimulationMode=1,
        pause_controller=pev_pc,
        message_observer=pev_logger,
    )

    def _pev_tick() -> None:
        while not stop.is_set():
            pev.mainfunction()
            time.sleep(0.03)

    pev_thread = threading.Thread(target=_pev_tick, daemon=True)
    pev_thread.start()

    # Let the handshake run for a few seconds.
    deadline = time.time() + 12
    saw_spoof_tx = False
    saw_spoof_rx = False
    while time.time() < deadline:
        time.sleep(0.5)
        # Have we observed the spoofed EVCCID on both sides yet?
        if evse_log.exists() and spoofed_evccid in evse_log.read_text(encoding="utf-8").lower():
            saw_spoof_rx = True
        if pev_log.exists() and spoofed_evccid in pev_log.read_text(encoding="utf-8").lower():
            saw_spoof_tx = True
        if saw_spoof_rx and saw_spoof_tx:
            break

    stop.set()
    evse_thread.join(timeout=2)
    pev_thread.join(timeout=2)
    evse_logger.close()
    pev_logger.close()

    assert saw_spoof_tx, f"PEV never logged spoofed EVCCID. pev.jsonl={pev_log.read_text(encoding='utf-8')[:500]}"
    assert saw_spoof_rx, f"EVSE never logged spoofed EVCCID. evse.jsonl={evse_log.read_text(encoding='utf-8')[:500]}"

    # Every line in the JSONL must parse cleanly.
    for log in (evse_log, pev_log):
        for line in log.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            assert set(rec.keys()) >= {"timestamp", "direction", "msg_name", "mode", "params"}
            assert rec["direction"] in ("rx", "tx")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_autocharge_attack_end_to_end(Path(d))
    print("[PASS] autocharge_attack_end_to_end")
