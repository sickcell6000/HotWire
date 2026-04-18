"""Unit tests for PauseController override + intercept behavior."""
from __future__ import annotations

import os
import sys
import threading
import time
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
    # Mutating either the caller's dict or the returned copy must not affect the stored override.
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


def test_pause_with_override_shows_merged_to_gui() -> None:
    """When pause is enabled AND override is set, the GUI callback and
    pending dict must reflect the merged values."""
    pc = PauseController()
    pc.set_override("A", {"x": 99})
    pc.set_pause_enabled("A", True)

    captured: list[tuple[str, dict]] = []

    def gui_cb(stage: str, params: dict) -> None:
        captured.append((stage, params))
        # Pretend the GUI finishes editing right away so the FSM can proceed.
        pc.send({"x": 99, "y": 42})

    pc.register_gui_callback(gui_cb)

    # FSM thread — calls intercept; should block until gui_cb has sent.
    result_holder: dict[str, object] = {}

    def fsm_thread() -> None:
        result_holder["r"] = pc.intercept("A", {"x": 1, "y": 2})

    t = threading.Thread(target=fsm_thread)
    t.start()
    t.join(timeout=2.0)
    assert not t.is_alive(), "intercept did not return"

    assert captured == [("A", {"x": 99, "y": 2})], (
        "GUI must see merged dict, not raw defaults"
    )
    assert result_holder["r"] == {"x": 99, "y": 42}


def test_abort_still_honors_override() -> None:
    """If the user aborts the pause dialog, the override still applies."""
    pc = PauseController()
    pc.set_override("A", {"x": 42})
    pc.set_pause_enabled("A", True)

    def gui_cb(stage: str, params: dict) -> None:
        pc.abort()

    pc.register_gui_callback(gui_cb)

    out: dict[str, object] = {}

    def fsm_thread() -> None:
        out["r"] = pc.intercept("A", {"x": 1, "y": 2})

    t = threading.Thread(target=fsm_thread)
    t.start()
    t.join(timeout=2.0)
    assert out["r"] == {"x": 42, "y": 2}


def test_fast_path_without_pause_or_override() -> None:
    """Baseline regression: with no pause and no override, intercept is pass-through."""
    pc = PauseController()
    defaults = {"x": 1}
    result = pc.intercept("A", defaults)
    assert result == {"x": 1}
    # Result must be a copy, not the same object, to prevent downstream mutation
    # bleeding into caller state.
    assert result is not defaults


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
