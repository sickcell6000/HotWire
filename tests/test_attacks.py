"""Unit tests for the attack playbooks.

Pure-Python — no Qt, no sockets. Every test constructs a PauseController,
applies an attack, and asserts the expected override is present.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "HOTWIRE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"),
)

from hotwire.attacks import AutochargeImpersonation, ForcedDischarge  # noqa: E402
from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE  # noqa: E402
from hotwire.fsm.pause_controller import PauseController  # noqa: E402


# --- AutochargeImpersonation --------------------------------------------


def test_autocharge_mode_is_pev():
    a = AutochargeImpersonation(evccid="d83add22f182")
    assert a.mode == C_PEV_MODE


def test_autocharge_lowercases_evccid():
    a = AutochargeImpersonation(evccid="D83ADD22F182")
    assert a.overrides["SessionSetupReq"]["EVCCID"] == "d83add22f182"


@pytest.mark.parametrize("bad", [
    "",                          # empty
    "123",                       # too short
    "d83add22f18",               # 11 chars
    "d83add22f1820",             # 13 chars
    "d83add22fzzz",              # non-hex
    "d8:3a:dd:22:f1:82",         # colons
])
def test_autocharge_rejects_bad_evccid(bad):
    with pytest.raises(ValueError):
        AutochargeImpersonation(evccid=bad)


def test_autocharge_apply_and_clear_roundtrip():
    pc = PauseController()
    a = AutochargeImpersonation(evccid="aabbccddeeff")
    a.apply(pc)
    assert pc.get_override("SessionSetupReq") == {"EVCCID": "aabbccddeeff"}
    a.clear(pc)
    assert pc.get_override("SessionSetupReq") is None


def test_autocharge_describe_contains_key_details():
    a = AutochargeImpersonation(evccid="d83add22f182")
    text = a.describe()
    assert "Autocharge" in text
    assert "d83add22f182" in text
    assert "PEV" in text
    assert "SessionSetupReq" in text


def test_autocharge_override_changes_intercept_output():
    """End-to-end: PauseController.intercept() with the attack installed
    must return a dict containing the spoofed EVCCID regardless of what
    default the FSM passes in."""
    pc = PauseController()
    AutochargeImpersonation(evccid="deadbeef0000").apply(pc)
    merged = pc.intercept("SessionSetupReq", {"EVCCID": "000000000000"})
    assert merged["EVCCID"] == "deadbeef0000"


# --- ForcedDischarge -----------------------------------------------------


def test_discharge_mode_is_evse():
    a = ForcedDischarge(voltage=380)
    assert a.mode == C_EVSE_MODE


def test_discharge_stores_voltage():
    a = ForcedDischarge(voltage=420)
    assert a.overrides["PreChargeRes"]["EVSEPresentVoltage"] == 420


def test_discharge_also_covers_current_demand():
    """A2 extension — the sustained-discharge attack must also override
    CurrentDemandRes so the EV keeps the contactors closed through the
    whole charging loop, not just PreCharge."""
    a = ForcedDischarge(voltage=380, current=10)
    assert "CurrentDemandRes" in a.overrides
    cdr = a.overrides["CurrentDemandRes"]
    assert cdr["EVSEPresentVoltage"] == 380
    assert cdr["EVSEPresentCurrent"] == 10


def test_discharge_current_defaults_to_safe_value():
    """Default current is small but nonzero — enough that the EV sees
    'charging is happening' without drawing dangerous amps."""
    a = ForcedDischarge(voltage=380)
    assert a.overrides["CurrentDemandRes"]["EVSEPresentCurrent"] == 1


def test_discharge_rejects_out_of_range_current():
    import pytest
    with pytest.raises(ValueError):
        ForcedDischarge(voltage=380, current=-1)
    with pytest.raises(ValueError):
        ForcedDischarge(voltage=380, current=10000)


@pytest.mark.parametrize("bad", [0, -1, 1001, 10000])
def test_discharge_rejects_out_of_range_voltage(bad):
    with pytest.raises(ValueError):
        ForcedDischarge(voltage=bad)


def test_discharge_apply_and_clear_roundtrip():
    pc = PauseController()
    a = ForcedDischarge(voltage=400)
    a.apply(pc)
    assert pc.get_override("PreChargeRes") == {"EVSEPresentVoltage": 400}
    a.clear(pc)
    assert pc.get_override("PreChargeRes") is None


def test_discharge_override_changes_intercept_output():
    pc = PauseController()
    ForcedDischarge(voltage=999).apply(pc)
    merged = pc.intercept("PreChargeRes", {"EVSEPresentVoltage": 350})
    assert merged["EVSEPresentVoltage"] == 999


# --- Cross-attack composition -------------------------------------------


def test_two_attacks_touching_different_stages_compose():
    """Installing both attacks at once should preserve both overrides."""
    pc = PauseController()
    AutochargeImpersonation(evccid="aaaaaaaaaaaa").apply(pc)
    ForcedDischarge(voltage=450).apply(pc)
    assert pc.get_override("SessionSetupReq") == {"EVCCID": "aaaaaaaaaaaa"}
    assert pc.get_override("PreChargeRes") == {"EVSEPresentVoltage": 450}


if __name__ == "__main__":
    import traceback
    tests = [(k, v) for k, v in list(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for name, t in tests:
        # pytest's @pytest.mark.parametrize stores the param tuples under
        # ``pytestmark`` on the function. When we're running without pytest
        # we have to expand them ourselves — otherwise t() gets called with
        # zero args and crashes with "missing positional argument".
        parametrize_args: list[tuple] = []
        param_names: list[str] = []
        for mark in getattr(t, "pytestmark", []):
            if getattr(mark, "name", None) == "parametrize":
                raw_argnames, argvals = mark.args[0], mark.args[1]
                param_names = [
                    s.strip() for s in (
                        raw_argnames.split(",") if isinstance(raw_argnames, str)
                        else raw_argnames
                    )
                ]
                for vals in argvals:
                    if not isinstance(vals, (list, tuple)):
                        vals = (vals,)
                    parametrize_args.append(tuple(vals))
                break

        if parametrize_args:
            for case in parametrize_args:
                label = f"{name}[{','.join(repr(v) for v in case)}]"
                try:
                    t(*case)
                    print(f"[PASS] {label}")
                except Exception:                                # noqa: BLE001
                    fails += 1
                    print(f"[FAIL] {label}")
                    traceback.print_exc()
        else:
            try:
                t()
                print(f"[PASS] {name}")
            except Exception:                                    # noqa: BLE001
                fails += 1
                print(f"[FAIL] {name}")
                traceback.print_exc()
    sys.exit(0 if fails == 0 else 1)
