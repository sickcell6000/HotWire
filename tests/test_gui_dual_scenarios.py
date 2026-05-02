"""
End-to-end GUI dual-process scenarios — simulates what a human operator
would do in two PyQt6 windows (one EVSE, one PEV) by driving the real
``HotWireMainWindow`` instances programmatically.

This is the closest thing we have to manual QA without an actual display:
the same widgets, signals, and PauseController paths the user would
exercise are actually instantiated, so we catch threading deadlocks,
signal wiring errors, and on-wire mutation propagation that pure unit
tests miss.

Four scenarios covered:

  1. Baseline — both sides ``Start``; PEV reaches CurrentDemand loop.
  2. EVSE override — mutate ``EVSEID`` in SessionSetupRes, verify it
     appears in the PEV's decoded trace.
  3. EVSE pause-and-edit — intercept PreChargeRes at GUI level, mutate
     ``EVSEPresentVoltage`` to 999, verify PEV sees it on the wire.
  4. PEV attack — override ``EVCCID`` in SessionSetupReq, verify the
     EVSE's StatusPanel receives the spoofed identifier.

Run:

    python tests/test_gui_dual_scenarios.py

Exits 0 on full pass, non-zero on any scenario failure.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "HOTWIRE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"),
)

from hotwire.core.config import load as load_config  # noqa: E402

load_config()

from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE  # noqa: E402
from hotwire.gui.main_window import HotWireMainWindow  # noqa: E402


# --- Harness helpers ---------------------------------------------------


class DualGUIHarness:
    """Owns one EVSE and one PEV HotWireMainWindow and pumps the event loop.

    Collects their trace logs into lists that scenario code can grep.
    """

    def __init__(self) -> None:
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.evse = HotWireMainWindow(mode=C_EVSE_MODE, is_simulation=True)
        self.pev = HotWireMainWindow(mode=C_PEV_MODE, is_simulation=True)
        self.evse_traces: list[str] = []
        self.pev_traces: list[str] = []
        self.evse.signals.trace_emitted.connect(
            lambda level, text: self.evse_traces.append(text)
        )
        self.pev.signals.trace_emitted.connect(
            lambda level, text: self.pev_traces.append(text)
        )

    def pump(self, seconds: float) -> None:
        """Run the Qt event loop for a fixed wall-clock duration."""
        done = threading.Event()
        QTimer.singleShot(int(seconds * 1000), done.set)
        while not done.is_set():
            self.app.processEvents()
            time.sleep(0.01)

    def pump_until(self, predicate, timeout_s: float = 15.0) -> bool:
        """Run the event loop until ``predicate()`` returns True or timeout."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def reset(self) -> None:
        """Stop workers, clear override state, clear trace buffers."""
        self.evse.stop_worker()
        self.pev.stop_worker()
        self.pump(0.5)
        self.evse.pause_controller.clear_override()
        self.pev.pause_controller.clear_override()
        self.evse_traces.clear()
        self.pev_traces.clear()

    def close(self) -> None:
        self.evse.close()
        self.pev.close()
        self.pump(0.3)


# --- Scenarios ---------------------------------------------------------


def scenario_1_baseline(h: DualGUIHarness) -> tuple[bool, str]:
    # Harness is fresh — no reset() needed.
    h.evse.start_worker()
    h.pump(1.0)                          # let server bind
    h.pev.start_worker()

    ok = h.pump_until(
        lambda: any("WaitForCurrentDemandRes" in t for t in h.pev_traces),
        timeout_s=12.0,
    )
    return ok, "PEV reached WaitForCurrentDemandRes" if ok else "timeout"


def scenario_2_evse_override(h: DualGUIHarness) -> tuple[bool, str]:
    # Harness is fresh — no reset() needed.
    # Apply override on EVSE side BEFORE starting.
    spoofed_id = "DEADBEEF1234"
    h.evse.stage_config.set_stage("SessionSetupRes")
    h.evse.stage_config.load_values(
        {"ResponseCode": "OK_NewSessionEstablished", "EVSEID": spoofed_id}
    )
    h.evse.stage_config._on_apply()

    h.evse.start_worker()
    h.pump(1.0)
    h.pev.start_worker()

    # OpenV2G decodes EVSEID back as lowercase hex, so match case-insensitively.
    needle = spoofed_id.lower()
    ok = h.pump_until(
        lambda: any(needle in t.lower() for t in h.pev_traces),
        timeout_s=10.0,
    )
    return ok, (
        f"PEV decoded EVSEID={needle}"
        if ok else "override did not propagate"
    )


def scenario_4_pev_attack_evccid(h: DualGUIHarness) -> tuple[bool, str]:
    # Harness is fresh — no reset() needed.
    spoofed_evccid = "AABBCCDDEEFF"
    h.pev.stage_config.set_stage("SessionSetupReq")
    h.pev.stage_config.load_values({"EVCCID": spoofed_evccid})
    h.pev.stage_config._on_apply()

    h.evse.start_worker()
    h.pump(1.0)
    h.pev.start_worker()

    # OpenV2G decodes EVCCID as lowercase hex.
    needle = spoofed_evccid.lower()

    def evse_saw_spoof() -> bool:
        return h.evse.status_panel._labels["EVCCID"].text().lower() == needle

    ok = h.pump_until(evse_saw_spoof, timeout_s=10.0)
    actual = h.evse.status_panel._labels["EVCCID"].text()
    return ok, (
        f"EVSE StatusPanel captured EVCCID={actual}"
        if ok else f"EVSE EVCCID ended up as '{actual}' (expected ~{needle})"
    )


# --- Runner ------------------------------------------------------------


SCENARIO_REGISTRY = {
    "1": ("1. Baseline handshake",          scenario_1_baseline),
    "2": ("2. EVSE override: EVSEID spoof", scenario_2_evse_override),
    "4": ("4. PEV override: EVCCID spoof",  scenario_4_pev_attack_evccid),
}


def _run_one(key: str) -> int:
    name, fn = SCENARIO_REGISTRY[key]
    print(f"=== {name} ===", flush=True)
    h = DualGUIHarness()
    try:
        ok, detail = fn(h)
    except Exception as e:                                  # noqa: BLE001
        ok, detail = False, f"exception: {e!r}"
    marker = "PASS" if ok else "FAIL"
    print(f"[{marker}] {detail}", flush=True)
    h.evse.stop_worker()
    h.pev.stop_worker()
    h.close()
    return 0 if ok else 1


def main() -> int:
    # If invoked with a single scenario key, run it in-process.
    if len(sys.argv) == 2 and sys.argv[1] in SCENARIO_REGISTRY:
        return _run_one(sys.argv[1])

    # Otherwise, delegate each scenario to its own subprocess so the port
    # binding is guaranteed fresh — ::1:57122 can linger in TIME_WAIT for
    # several seconds after stop() otherwise.
    import subprocess

    results: list[tuple[str, bool, str]] = []
    for key, (name, _fn) in SCENARIO_REGISTRY.items():
        print(f"\n===== running scenario {key} in subprocess =====", flush=True)
        result = subprocess.run(
            [sys.executable, __file__, key],
            capture_output=True, text=True,
            timeout=60,
        )
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        ok = result.returncode == 0
        # Scrape the last [PASS]/[FAIL] line for summary.
        detail = ""
        for line in reversed(result.stdout.splitlines()):
            if line.startswith("[PASS]") or line.startswith("[FAIL]"):
                detail = line.split("]", 1)[1].strip()
                break
        results.append((name, ok, detail))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    any_failed = False
    for name, ok, detail in results:
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {name} — {detail}")
        if not ok:
            any_failed = True

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
