"""Unit tests for PauseController override behavior.

Despite the file/class name (kept for compat), the controller is now
override-only — the interactive pause/abort/release path was removed
because it conflicted with DIN 70121 §9.6 spec timeouts on real
vehicles.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.fsm.pause_controller import PauseController  # noqa: E402


def test_override_merges_into_intercept() -> None:
    pc = PauseController()
    pc.set_override("PreChargeRes", {"EVSEPresentVoltage": 999})
    result = pc.intercept("PreChargeRes", {"EVSEPresentVoltage": 350, "Other": 1})
    assert result == {"EVSEPresentVoltage": 999, "Other": 1}


def test_override_isolated_per_stage() -> None:
    pc = PauseController()
    pc.set_override("PreChargeRes", {"v": 1})
    pc.set_override("SessionSetupRes", {"v": 2})
    assert pc.intercept("PreChargeRes", {"v": 0}) == {"v": 1}
    assert pc.intercept("SessionSetupRes", {"v": 0}) == {"v": 2}
    assert pc.intercept("OtherStage", {"v": 0}) == {"v": 0}


def test_clear_single_override() -> None:
    pc = PauseController()
    pc.set_override("A", {"x": 1})
    pc.set_override("B", {"x": 2})
    pc.clear_override("A")
    assert pc.intercept("A", {"x": 0}) == {"x": 0}
    assert pc.intercept("B", {"x": 0}) == {"x": 2}


def test_clear_all_overrides() -> None:
    pc = PauseController()
    pc.set_override("A", {"x": 1})
    pc.set_override("B", {"x": 2})
    pc.clear_override()
    assert pc.intercept("A", {"x": 0}) == {"x": 0}
    assert pc.intercept("B", {"x": 0}) == {"x": 0}


def test_has_override() -> None:
    pc = PauseController()
    assert not pc.has_override("A")
    pc.set_override("A", {"x": 1})
    assert pc.has_override("A")
    pc.clear_override("A")
    assert not pc.has_override("A")


def test_get_override_returns_copy() -> None:
    pc = PauseController()
    original = {"x": 1}
    pc.set_override("A", original)
    # Mutating either the caller's dict or the returned copy must not
    # affect the stored override.
    original["x"] = 99
    got = pc.get_override("A")
    assert got == {"x": 1}
    got["x"] = 77
    assert pc.get_override("A") == {"x": 1}


def test_override_does_not_mutate_caller_defaults() -> None:
    pc = PauseController()
    pc.set_override("A", {"x": 2})
    defaults = {"x": 1, "y": 3}
    pc.intercept("A", defaults)
    # Caller dict must remain unchanged.
    assert defaults == {"x": 1, "y": 3}


def test_fast_path_without_override() -> None:
    """Baseline regression: with no override, intercept is pass-through."""
    pc = PauseController()
    defaults = {"x": 1}
    result = pc.intercept("A", defaults)
    assert result == {"x": 1}
    # Result must be a copy, not the same object, to prevent downstream
    # mutation bleeding into caller state.
    assert result is not defaults


def test_intercept_drops_empty_string_overrides() -> None:
    """Pin the SessionID-poisoning fix from commit 66fb6d9.

    A scripted ``set_override({"SessionID": ""})`` call (or a future
    Attack subclass with an empty-string default) must NOT clobber the
    FSM's runtime SessionID. Empty string in an override means
    "operator left the field blank, please keep the default" — never
    "operator wants this on the wire literally empty", which would
    shift OpenV2G's positional EXI args and produce malformed
    PreChargeReq / SessionSetupReq frames.
    """
    pc = PauseController()
    pc.set_override("PreChargeReq", {
        "SessionID": "",                  # poison from a stale form widget
        "EVTargetVoltage": 500,
    })
    defaults = {
        "SessionID": "0102030405060708",  # FSM-tracked real value
        "SoC": "30",
        "EVTargetVoltage": "350",
        "EVTargetCurrent": "1",
    }
    merged = pc.intercept("PreChargeReq", defaults)
    assert merged["SessionID"] == "0102030405060708", (
        "empty SessionID override leaked through and clobbered the "
        "FSM-supplied value"
    )
    assert merged["EVTargetVoltage"] == 500, (
        "non-empty override did not apply"
    )
    assert merged["SoC"] == "30"


def test_intercept_keeps_zero_and_false_overrides() -> None:
    """Companion to the empty-string filter: numeric ``0`` and boolean
    ``False`` are intentional values, not absences, and must reach the
    FSM. (Regression: a too-aggressive filter that dropped any
    falsy value would silently break attacks like
    ``ForcedDischarge(current=0)``.)"""
    pc = PauseController()
    pc.set_override("PreChargeRes", {
        "EVSEPresentCurrent": 0,           # legitimate 0 amps
        "IsolationStatusUsed": False,      # legitimate "don't report"
    })
    merged = pc.intercept("PreChargeRes", {"EVSEPresentVoltage": 400})
    assert merged["EVSEPresentCurrent"] == 0
    assert merged["IsolationStatusUsed"] is False
    assert merged["EVSEPresentVoltage"] == 400


if __name__ == "__main__":
    # Simple runner — easier than full pytest in some CI configs.
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:                                      # noqa: BLE001
            failures += 1
            print(f"[ERROR] {t.__name__}: {e}")
    sys.exit(0 if failures == 0 else 1)
