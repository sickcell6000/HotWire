"""End-to-end test: sustained discharge override propagates through the
full DIN 70121 handshake into the CurrentDemand loop.

This is the harder sibling of ``test_attack_integration.py`` which only
verifies the Autocharge (EVCCID) override. Here we verify that
``ForcedDischarge`` — which adds a ``CurrentDemandRes`` override on top
of the existing ``PreChargeRes`` override — actually makes the EVSE's
27-argument ``EDi_...`` command builder produce valid EXI that the PEV
can decode, and that the override values (``EVSEPresentVoltage``,
``EVSEPresentCurrent``) reach the wire.

Why this matters: the paper's Attack 2 ("unauthorized energy extraction
via BMS state confusion") relies on the attacker *continually* lying
about present voltage throughout the CurrentDemand loop, not just during
PreCharge. If the EDi builder produces malformed EXI, the EV would
abort the session — and the attack would fail silently. This test
catches that regression.

The test spawns two HotWireWorkers inside the same Python process and
drives them through a real handshake; both sides log every decoded
message to in-memory lists. We then grep for the expected values.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini")
)

from hotwire.attacks import ForcedDischarge  # noqa: E402
from hotwire.core.config import load as load_config  # noqa: E402

load_config()

from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE  # noqa: E402
from hotwire.core.worker import HotWireWorker  # noqa: E402
from hotwire.fsm.pause_controller import PauseController  # noqa: E402


class _RecordingObserver:
    """Plain-Python MessageObserver that stores every event for later
    inspection. Thread-safe append, no locking needed for the test."""

    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def on_message(self, direction: str, msg_name: str, params: dict) -> None:
        self.events.append((direction, msg_name, dict(params)))


def test_forced_discharge_propagates_to_current_demand():
    # The PEV will only exit PreCharge when the announced EVSEPresentVoltage
    # is within ``u_delta_max_for_end_of_precharge`` volts (default 10)
    # of its own battery voltage. The SimulatedHardwareInterface reports
    # 350 V for accu_voltage, so we pick a spoofed voltage inside that
    # tolerance — otherwise the PEV would loop on PreChargeReq forever
    # and never reach CurrentDemand. (Real attacks against a ~400 V EV
    # would use voltage=395-405; we can't hit that in sim without also
    # hacking SimulatedHardwareInterface.)
    fake_voltage = 352
    fake_current = 7

    attack = ForcedDischarge(voltage=fake_voltage, current=fake_current)
    # The attack must touch both stages — otherwise the "sustained" part
    # of the attack would be broken and this test is pointless.
    assert "PreChargeRes" in attack.overrides
    assert "CurrentDemandRes" in attack.overrides

    # Build two workers: attacker EVSE gets the playbook installed, honest PEV
    # doesn't override anything and will just decode whatever it receives.
    # When pytest runs this alongside other loopback tests in one process,
    # port 57122 can be held in Windows TIME_WAIT for up to 2 minutes. The
    # HotWireWorker's TCP server binds without SO_REUSEADDR, so a probe
    # that uses SO_REUSEADDR is an unreliable check. Instead, we skip the
    # test if a non-reusable bind fails — the standalone
    # ``scripts/run_all_tests.py`` subprocess runner always sees a fresh
    # port and does exercise this path.
    import socket as _socket
    from hotwire.plc.tcp_socket import _resolve_tcp_port
    port = _resolve_tcp_port()
    probe = _socket.socket(_socket.AF_INET6, _socket.SOCK_STREAM)
    try:
        probe.bind(("::1", port, 0, 0))
        probe.close()
    except OSError as e:
        probe.close()
        import pytest
        pytest.skip(
            f"port {port} busy ({e}); run via "
            f"`python scripts/run_all_tests.py` for a clean subprocess"
        )

    evse_pc = PauseController()
    pev_pc = PauseController()
    attack.apply(evse_pc)

    evse_obs = _RecordingObserver()
    pev_obs = _RecordingObserver()

    evse = HotWireWorker(
        callbackAddToTrace=lambda s: None,
        callbackShowStatus=lambda *a, **kw: None,
        mode=C_EVSE_MODE,
        isSimulationMode=1,
        pause_controller=evse_pc,
        message_observer=evse_obs,
    )
    stop = threading.Event()

    def _evse_tick():
        while not stop.is_set():
            try:
                evse.mainfunction()
            except Exception:                                   # noqa: BLE001
                pass
            time.sleep(0.03)

    t_evse = threading.Thread(target=_evse_tick, daemon=True)
    t_evse.start()

    # Give the TCP server time to bind before the PEV tries to connect.
    time.sleep(1.5)

    pev = HotWireWorker(
        callbackAddToTrace=lambda s: None,
        callbackShowStatus=lambda *a, **kw: None,
        mode=C_PEV_MODE,
        isSimulationMode=1,
        pause_controller=pev_pc,
        message_observer=pev_obs,
    )

    def _pev_tick():
        while not stop.is_set():
            try:
                pev.mainfunction()
            except Exception:                                   # noqa: BLE001
                pass
            time.sleep(0.03)

    t_pev = threading.Thread(target=_pev_tick, daemon=True)
    t_pev.start()

    # Wait up to 15 seconds for sustained CurrentDemand traffic to appear
    # with the spoofed voltage.
    def pev_saw_spoofed_voltage_in_current_demand() -> bool:
        for direction, name, params in pev_obs.events:
            if direction != "rx" or name != "CurrentDemandRes":
                continue
            # OpenV2G decodes voltage as Value + Multiplier pair.
            val = params.get("EVSEPresentVoltage.Value")
            mult = params.get("EVSEPresentVoltage.Multiplier", "0")
            try:
                actual = int(val) * (10 ** int(mult))
            except (TypeError, ValueError):
                continue
            if actual == fake_voltage:
                return True
        return False

    def pev_saw_spoofed_current_in_current_demand() -> bool:
        for direction, name, params in pev_obs.events:
            if direction != "rx" or name != "CurrentDemandRes":
                continue
            val = params.get("EVSEPresentCurrent.Value")
            mult = params.get("EVSEPresentCurrent.Multiplier", "0")
            try:
                actual = int(val) * (10 ** int(mult))
            except (TypeError, ValueError):
                continue
            if actual == fake_current:
                return True
        return False

    deadline = time.time() + 15
    got_voltage = got_current = False
    while time.time() < deadline:
        time.sleep(0.5)
        got_voltage = pev_saw_spoofed_voltage_in_current_demand()
        got_current = pev_saw_spoofed_current_in_current_demand()
        if got_voltage and got_current:
            break

    stop.set()
    t_evse.join(timeout=2)
    t_pev.join(timeout=2)

    # Collect diagnostics regardless of pass/fail — makes regressions easy
    # to triage.
    cdr_events = [
        e for e in pev_obs.events
        if e[0] == "rx" and e[1] == "CurrentDemandRes"
    ]
    cdr_voltages = [
        (e[2].get("EVSEPresentVoltage.Value"),
         e[2].get("EVSEPresentVoltage.Multiplier"))
        for e in cdr_events
    ]
    cdr_currents = [
        (e[2].get("EVSEPresentCurrent.Value"),
         e[2].get("EVSEPresentCurrent.Multiplier"))
        for e in cdr_events
    ]

    assert cdr_events, (
        "PEV never received a CurrentDemandRes — handshake did not reach "
        f"CurrentDemand loop within 15s. PEV rx names: "
        f"{sorted({n for _, n, _ in pev_obs.events})}"
    )
    assert got_voltage, (
        f"PEV saw {len(cdr_events)} CurrentDemandRes messages but none had "
        f"EVSEPresentVoltage={fake_voltage}. Observed voltages: {cdr_voltages[:5]}"
    )
    assert got_current, (
        f"PEV saw {len(cdr_events)} CurrentDemandRes messages but none had "
        f"EVSEPresentCurrent={fake_current}. Observed currents: {cdr_currents[:5]}"
    )


if __name__ == "__main__":
    try:
        test_forced_discharge_propagates_to_current_demand()
        print("[PASS] test_forced_discharge_propagates_to_current_demand")
    except AssertionError as e:
        print(f"[FAIL] test_forced_discharge_propagates_to_current_demand\n{e}")
        sys.exit(1)
