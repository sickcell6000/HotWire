"""
End-to-end test: spawn a HotWire EVSE and PEV in two subprocesses and
verify the full DIN 70121 session reaches the CurrentDemand charging
loop within a few seconds over ::1 loopback.

Exit code 0 = PASS, anything else = FAIL.

Run with:

    python tests/test_two_process_loopback.py

Or with pytest:

    pytest tests/test_two_process_loopback.py -s
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVSE_SCRIPT = PROJECT_ROOT / "scripts" / "run_evse.py"
PEV_SCRIPT = PROJECT_ROOT / "scripts" / "run_pev.py"
CONFIG = PROJECT_ROOT / "config" / "hotwire.ini"

# Milestones we want to observe in the PEV log to consider the session "working".
REQUIRED_PEV_MILESTONES = [
    "entering 2:Connected",
    "entering 3:WaitForAppProtocolRes",
    "entering 4:WaitForSessionSetupRes",
    "entering 5:WaitForServiceDiscoveryRes",
    "entering 6:WaitForServicePaymentRes",
    "entering 7:WaitForContractAuthRes",
    "entering 8:WaitForChargeParamRes",
    "entering 9:WaitForConnectorLock",
    "entering 10:WaitForCableCheckRes",
    "entering 11:WaitForPreChargeRes",
    "entering 12:WaitForContactorsClosed",
    "entering 13:WaitForPowerDeliveryRes",
    "entering 14:WaitForCurrentDemandRes",
]

REQUIRED_EVSE_MILESTONES = [
    "entering state 1:WaitForSessionSetup",
    "entering state 2:WaitForServiceDiscovery",
    "entering state 3:WaitForServicePayment",
    "entering state 4:WaitForFlexibleRequest",
]


def _spawn(script: Path, log_path: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["HOTWIRE_CONFIG"] = str(CONFIG)
    env["PYTHONUNBUFFERED"] = "1"
    log_fh = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, str(script)],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        env=env,
    )


def _log_contains_all(log_path: Path, needles: list[str]) -> tuple[bool, list[str]]:
    """Return (all_found, list_of_missing_needles)."""
    if not log_path.exists():
        return False, needles[:]
    text = log_path.read_text(encoding="utf-8", errors="replace")
    missing = [n for n in needles if n not in text]
    return not missing, missing


def test_two_process_loopback(timeout_s: float = 20.0) -> bool:
    evse_log = Path("/tmp/hotwire_evse_test.log")
    pev_log = Path("/tmp/hotwire_pev_test.log")

    print(f"[test] spawning EVSE: {EVSE_SCRIPT}")
    evse = _spawn(EVSE_SCRIPT, evse_log)
    try:
        time.sleep(2.0)  # let the server bind before the client attempts connect
        if evse.poll() is not None:
            print(f"[FAIL] EVSE exited early with code {evse.returncode}")
            print(evse_log.read_text(encoding="utf-8", errors="replace"))
            return False

        print(f"[test] spawning PEV: {PEV_SCRIPT}")
        pev = _spawn(PEV_SCRIPT, pev_log)
        try:
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                time.sleep(0.5)
                pev_ok, pev_missing = _log_contains_all(pev_log, REQUIRED_PEV_MILESTONES)
                evse_ok, evse_missing = _log_contains_all(evse_log, REQUIRED_EVSE_MILESTONES)
                if pev_ok and evse_ok:
                    print(
                        f"[PASS] Full DIN 70121 session reached CurrentDemand in "
                        f"~{timeout_s - (deadline - time.time()):.1f}s"
                    )
                    return True
            # Timeout.
            pev_ok, pev_missing = _log_contains_all(pev_log, REQUIRED_PEV_MILESTONES)
            evse_ok, evse_missing = _log_contains_all(evse_log, REQUIRED_EVSE_MILESTONES)
            print("[FAIL] Timeout waiting for session to complete")
            if not evse_ok:
                print(f"  EVSE missing: {evse_missing}")
            if not pev_ok:
                print(f"  PEV missing: {pev_missing}")
            # Dump tail for diagnosis.
            for label, path in (("EVSE", evse_log), ("PEV", pev_log)):
                if path.exists():
                    print(f"--- tail {label} ---")
                    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in text[-30:]:
                        print(f"  {line}")
            return False
        finally:
            pev.terminate()
            try:
                pev.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                pev.kill()
    finally:
        evse.terminate()
        try:
            evse.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            evse.kill()


if __name__ == "__main__":
    ok = test_two_process_loopback()
    sys.exit(0 if ok else 1)
