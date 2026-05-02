"""
Simulation-mode attack integration tests.

Exercises Attack A1 (EVCCID Impersonation) and Attack A2 (Forced
Discharge) against the loopback simulation transport — no PLC modems,
no Pi, no Windows host. This is what runs in the Docker CI suite and
what reviewers will hit on a fresh clone with no hardware.

Each test:
  1. Spawns EVSE + PEV ``HotWireWorker``s in ``isSimulationMode=1``
  2. Installs the named attack on the appropriate side
  3. Tick both for a few seconds
  4. Asserts the fabricated values reached the wire

ISOLATION: simulation-mode workers share a process-wide loopback
transport state (the ``::1:57122`` listening socket goes through OS
TIME_WAIT between sessions). To make the suite robust regardless of
ordering, each test launches the worker pair in a **fresh subprocess**
via this module's ``__main__`` runner — a pytest-forked-style approach
without the dependency. The parent test reads the child's stdout JSON
result and asserts on it. Single-threaded pytest runs all three tests
deterministically PASS.

Headless Qt; runs in CI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault(
    "HOTWIRE_CONFIG", str(_ROOT / "config" / "hotwire.ini"),
)


# ----- Subprocess runner ------------------------------------------------
#
# The body below executes when this module is invoked as ``python
# tests/test_attack_sim_mode.py --child <scenario>``. It runs the
# requested attack scenario, prints a JSON line with the wire-level
# evidence, and exits 0 on success / 1 on failure. The parent pytest
# tests just read that JSON.


def _child_run_two_workers(scenario: str) -> dict:
    """Spawn one EVSE+PEV pair in this Python process and return the
    wire-level results for the chosen scenario."""
    import threading                                            # noqa: F401
    import time

    from hotwire.core.config import load as load_config
    load_config()

    from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController

    from hotwire.attacks import AutochargeImpersonation, ForcedDischarge

    evse_pause = PauseController()
    pev_pause = PauseController()
    expected: dict = {}

    if scenario == "a1":
        sentinel = "deadbeefcafe"
        AutochargeImpersonation(evccid=sentinel).apply(pev_pause)
        expected["evccid"] = sentinel
    elif scenario == "a2":
        sentinel_v = 380
        ForcedDischarge(voltage=sentinel_v, current=10).apply(evse_pause)
        # Force the PEV to request a small EVTargetVoltage so the EVSE
        # FSM's default precharge ramp (25 V/round, ~1.5 s/round inside
        # the in-process test loop) reaches it within the test deadline.
        # The A2 attack itself doesn't touch PreChargeRes any more — the
        # default ramp mirrors whatever the PEV asks for, so the smaller
        # we ask for, the faster PreCharge finishes and CurrentDemand
        # (where the sentinel actually lands) begins.
        pev_pause.set_override("PreChargeReq", {"EVTargetVoltage": "50"})
        expected["voltage"] = sentinel_v
    elif scenario == "a1_combined":
        AutochargeImpersonation(evccid="aabbccddeeff").apply(pev_pause)
        pev_pause.set_override("PreChargeReq", {"EVTargetVoltage": "888"})
        expected["evccid"] = "aabbccddeeff"
        expected["voltage_pev"] = 888
    else:
        raise ValueError(f"unknown scenario: {scenario}")

    evse_traces: list[str] = []
    pev_traces: list[str] = []

    class _Obs(MessageObserver):
        def __init__(self):
            self.tx_stages: list[str] = []
            self.rx_stages: list[str] = []
        def on_message(self, direction, stage, params):
            (self.tx_stages if direction == "tx" else self.rx_stages).append(stage)

    evse_obs = _Obs()
    pev_obs = _Obs()

    evse = HotWireWorker(
        callbackAddToTrace=lambda s: evse_traces.append(s),
        mode=C_EVSE_MODE, isSimulationMode=1,
        pause_controller=evse_pause, message_observer=evse_obs,
    )
    pev = HotWireWorker(
        callbackAddToTrace=lambda s: pev_traces.append(s),
        mode=C_PEV_MODE, isSimulationMode=1,
        pause_controller=pev_pause, message_observer=pev_obs,
    )

    # 20 s: enough for PEV to clock through the full DIN handshake INCLUDING
    # the PreCharge ramp (default _PRECHARGE_RAMP_STEP_V = 25 V/round, so a
    # 350 V target takes ~14 round-trips before CurrentDemand starts).
    # Pre-2026-05 this was 8 s, which worked when A2 forced-overrode
    # PreChargeRes to a static value — but that broke real PEVs whose
    # EVTargetVoltage didn't match. The override was removed; tests now
    # exercise the realistic ramp + CurrentDemand path.
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            evse.mainfunction()
            pev.mainfunction()
        except Exception:    # noqa: BLE001
            break
        if (any(s == "CurrentDemandReq" for s in pev_obs.tx_stages)
                and any(s == "CurrentDemandRes" for s in pev_obs.rx_stages)):
            time.sleep(0.05)
            break
        time.sleep(0.01)

    for w in (evse, pev):
        try:
            w.shutdown()
        except Exception:    # noqa: BLE001
            pass

    return {
        "scenario": scenario,
        "expected": expected,
        "pev_traces_tail": pev_traces[-30:],
        "evse_traces_tail": evse_traces[-30:],
        "pev_tx_stages": pev_obs.tx_stages,
        "pev_rx_stages": pev_obs.rx_stages,
        "evse_tx_stages": evse_obs.tx_stages,
        "evse_rx_stages": evse_obs.rx_stages,
        # Pre-computed assertions (in-child) so the parent doesn't need
        # to re-pattern-match large trace lists.
        "pev_encoded_a1_evccid": any(
            f"EDA_{expected.get('evccid', '???')}" in t for t in pev_traces
        ),
        "evse_decoded_a1_evccid": any(
            expected.get("evccid", "???") in t for t in evse_traces
        ),
        # A2 sentinel now lands in CurrentDemandRes (E*i_ prefix), not
        # PreChargeRes (E*g_) — the latter mirrors the PEV's request via
        # the default ramp so PreCharge can complete. Match the value at
        # the EVSEPresentVoltage position (8th positional arg in EDi/EFi).
        "evse_encoded_a2_voltage": any(
            f"_{expected.get('voltage', -1)}_5_" in t
            and ("EDi_" in t or "EFi_" in t)
            for t in evse_traces
        ),
        "pev_decoded_a2_voltage": any(
            f'"EVSEPresentVoltage.Value": "{expected.get("voltage", -1)}"' in t
            for t in pev_traces
        ),
        "pev_encoded_combined_voltage": any(
            "EDG_" in t and f"_{expected.get('voltage_pev', -1)}_" in t
            for t in pev_traces
        ),
    }


def _run_child(scenario: str) -> dict:
    """Parent helper: launch this file as a child process for ``scenario``
    and parse the JSON line it prints to stdout. Each test gets a
    distinct loopback TCP port to dodge any port left bound by an
    earlier test in the parent suite."""
    # Hash to a stable port per scenario in IANA dynamic range.
    port_map = {"a1": 49152, "a2": 49153, "a1_combined": 49154}
    port = port_map.get(scenario, 49160)
    child_env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    # The config reader honours HOTWIRE_TCP_PORT_OVERRIDE if present
    # (added below) — but for back-compat with existing config we
    # also re-export via HOTWIRE_TCP_PORT_ALTERNATIVE which the
    # _resolve_tcp_port logic will pick up.
    child_env["HOTWIRE_TCP_PORT_OVERRIDE"] = str(port)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--child", scenario],
        capture_output=True, text=True, timeout=45,
        env=child_env,
    )
    # Find the JSON line — child prints one ``RESULT_JSON: {...}`` line
    # at the end. Anything else on stdout is benign trace noise.
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT_JSON:"):
            try:
                return json.loads(line[len("RESULT_JSON:"):].strip())
            except json.JSONDecodeError:
                pass
    raise RuntimeError(
        f"child returned no parseable RESULT_JSON for scenario {scenario!r}; "
        f"stdout tail: {proc.stdout[-500:]}; "
        f"stderr tail: {proc.stderr[-500:]}; rc={proc.returncode}"
    )


# ----- Pytest tests (parent side) ---------------------------------------


def test_a1_autocharge_impersonation_sim():
    """PEV-side override on SessionSetupReq.EVCCID lands on the wire."""
    res = _run_child("a1")
    assert res["pev_encoded_a1_evccid"], (
        f"PEV never encoded EDA_deadbeefcafe; "
        f"pev tx_stages={res['pev_tx_stages'][:10]}; "
        f"trace tail={res['pev_traces_tail']}"
    )
    assert res["evse_decoded_a1_evccid"], (
        f"EVSE never decoded deadbeefcafe; "
        f"evse rx_stages={res['evse_rx_stages'][:10]}"
    )


def test_a2_forced_discharge_sim():
    """EVSE-side override on PreChargeRes.EVSEPresentVoltage lands on
    the wire (PEV decodes the fabricated voltage)."""
    res = _run_child("a2")
    assert res["evse_encoded_a2_voltage"], (
        f"EVSE never encoded fabricated voltage 380; "
        f"evse tx_stages={res['evse_tx_stages'][:10]}; "
        f"trace tail={res['evse_traces_tail']}"
    )
    assert res["pev_decoded_a2_voltage"], (
        f"PEV never decoded EVSEPresentVoltage=380; "
        f"pev rx_stages={res['pev_rx_stages'][:10]}"
    )


def test_a1_combined_with_pev_v_override_sim():
    """A1 + an unrelated PreChargeReq voltage override don't interfere
    (regression for the 'concurrent attacks' phase 8 finding)."""
    res = _run_child("a1_combined")
    assert res["pev_encoded_a1_evccid"], "A1 EVCCID override didn't reach wire"
    assert res["pev_encoded_combined_voltage"], (
        "PreCharge voltage override didn't reach wire"
    )


# ----- Child-mode entry -------------------------------------------------


if __name__ == "__main__":
    if "--child" in sys.argv:
        idx = sys.argv.index("--child")
        scenario = sys.argv[idx + 1]
        try:
            result = _child_run_two_workers(scenario)
        except Exception as e:    # noqa: BLE001
            print(f"RESULT_JSON: {json.dumps({'error': str(e)})}")
            sys.exit(1)
        print(f"RESULT_JSON: {json.dumps(result)}")
        sys.exit(0)
    sys.exit(pytest.main([__file__, "-v"]))
