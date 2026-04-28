"""Unit tests for hotwire.fsm.pause_controller.PauseController.

Covers the public API exhaustively:
  - set_pause_enabled / is_paused_for / set_all_paused
  - set_override / get_override / clear_override / has_override
  - intercept (immediate-return + blocking) / send / abort
  - get_pending / is_currently_paused
  - register_gui_callback (callback fires; misbehaving callback doesn't deadlock)

These run pure-Python — no sockets, no hardware. They exist so a
reviewer can run ``pytest tests/test_pause_controller.py`` on a fresh
clone and validate the design's core "intercept-modify-release"
contract before touching any of the hardware-specific tests.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "HOTWIRE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"),
)

from hotwire.fsm.pause_controller import PauseController  # noqa: E402


# --- set_pause_enabled / is_paused_for ----------------------------------


def test_pause_disabled_by_default():
    pc = PauseController()
    assert pc.is_paused_for("PreChargeReq") is False
    assert pc.is_paused_for("CurrentDemandRes") is False


def test_set_pause_enabled_toggles():
    pc = PauseController()
    pc.set_pause_enabled("PreChargeReq", True)
    assert pc.is_paused_for("PreChargeReq") is True
    pc.set_pause_enabled("PreChargeReq", False)
    assert pc.is_paused_for("PreChargeReq") is False


def test_set_all_paused_with_explicit_stages():
    pc = PauseController()
    stages = ["SessionSetupReq", "PreChargeReq", "CurrentDemandReq"]
    pc.set_all_paused(True, stages=stages)
    for s in stages:
        assert pc.is_paused_for(s) is True
    # Stages we didn't list stay disabled
    assert pc.is_paused_for("ServiceDiscoveryReq") is False


def test_set_all_paused_false_clears_map():
    pc = PauseController()
    pc.set_pause_enabled("PreChargeReq", True)
    pc.set_pause_enabled("CurrentDemandReq", True)
    pc.set_all_paused(False)
    assert pc.is_paused_for("PreChargeReq") is False
    assert pc.is_paused_for("CurrentDemandReq") is False


# --- set_override / get_override / clear_override / has_override --------


def test_override_initially_absent():
    pc = PauseController()
    assert pc.has_override("PreChargeRes") is False
    assert pc.get_override("PreChargeRes") is None


def test_override_set_and_get_returns_copy():
    pc = PauseController()
    pc.set_override("PreChargeRes", {"EVSEPresentVoltage": 380})
    assert pc.has_override("PreChargeRes") is True
    got = pc.get_override("PreChargeRes")
    assert got == {"EVSEPresentVoltage": 380}
    # Mutating the returned dict must not affect the controller's state.
    got["EVSEPresentVoltage"] = 999
    assert pc.get_override("PreChargeRes") == {"EVSEPresentVoltage": 380}


def test_clear_override_one_stage():
    pc = PauseController()
    pc.set_override("PreChargeRes", {"V": 1})
    pc.set_override("CurrentDemandRes", {"V": 2})
    pc.clear_override("PreChargeRes")
    assert pc.has_override("PreChargeRes") is False
    assert pc.has_override("CurrentDemandRes") is True


def test_clear_override_all():
    pc = PauseController()
    pc.set_override("A", {"x": 1})
    pc.set_override("B", {"y": 2})
    pc.clear_override()
    assert pc.has_override("A") is False
    assert pc.has_override("B") is False


# --- intercept: no pause, no override -----------------------------------


def test_intercept_passthrough_when_pause_disabled():
    pc = PauseController()
    defaults = {"V": 220, "I": 5}
    out = pc.intercept("PreChargeReq", defaults)
    assert out == defaults
    # intercept must return a fresh dict — callers will mutate it
    out["V"] = 999
    assert pc.intercept("PreChargeReq", defaults)["V"] == 220


def test_intercept_passthrough_returns_independent_copy():
    pc = PauseController()
    defaults = {"V": 220}
    out = pc.intercept("PreChargeReq", defaults)
    assert out is not defaults


# --- intercept: override only (no pause) --------------------------------


def test_intercept_applies_override_when_no_pause():
    pc = PauseController()
    pc.set_override("PreChargeRes", {"EVSEPresentVoltage": 380})
    out = pc.intercept("PreChargeRes", {"EVSEPresentVoltage": 220, "extra": 1})
    assert out == {"EVSEPresentVoltage": 380, "extra": 1}


def test_intercept_override_only_overrides_listed_keys():
    pc = PauseController()
    pc.set_override("PreChargeRes", {"EVSEPresentVoltage": 380})
    out = pc.intercept(
        "PreChargeRes",
        {"EVSEPresentVoltage": 220, "ResponseCode": 0, "Foo": "bar"},
    )
    assert out["EVSEPresentVoltage"] == 380
    assert out["ResponseCode"] == 0
    assert out["Foo"] == "bar"


# --- intercept: pause + send --------------------------------------------


def test_pause_and_send_releases_with_modified_params():
    pc = PauseController()
    pc.set_pause_enabled("PreChargeReq", True)
    received = []

    def fsm() -> None:
        out = pc.intercept("PreChargeReq", {"V": 220, "I": 5})
        received.append(out)

    t = threading.Thread(target=fsm, daemon=True)
    t.start()
    # Wait briefly for fsm to land in intercept()
    deadline = time.monotonic() + 1.0
    while pc.get_pending() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert pc.is_currently_paused() is True
    pending = pc.get_pending()
    assert pending is not None
    assert pending["stage"] == "PreChargeReq"
    assert pending["params"] == {"V": 220, "I": 5}

    pc.send({"V": 999, "I": 5})
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert received == [{"V": 999, "I": 5}]
    assert pc.is_currently_paused() is False


def test_pause_and_abort_releases_with_original_params():
    pc = PauseController()
    pc.set_pause_enabled("PreChargeReq", True)
    received = []

    def fsm() -> None:
        out = pc.intercept("PreChargeReq", {"V": 220})
        received.append(out)

    t = threading.Thread(target=fsm, daemon=True)
    t.start()
    deadline = time.monotonic() + 1.0
    while pc.get_pending() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert pc.is_currently_paused() is True
    pc.abort()
    t.join(timeout=2.0)
    assert received == [{"V": 220}]
    assert pc.is_currently_paused() is False


def test_pause_with_send_none_uses_original_params():
    pc = PauseController()
    pc.set_pause_enabled("PreChargeReq", True)
    received = []

    def fsm() -> None:
        out = pc.intercept("PreChargeReq", {"V": 220})
        received.append(out)

    t = threading.Thread(target=fsm, daemon=True)
    t.start()
    deadline = time.monotonic() + 1.0
    while pc.get_pending() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    pc.send(None)
    t.join(timeout=2.0)
    assert received == [{"V": 220}]


# --- intercept: pause + override interaction ----------------------------


def test_pause_sees_override_merged_params():
    pc = PauseController()
    pc.set_override("PreChargeRes", {"EVSEPresentVoltage": 380})
    pc.set_pause_enabled("PreChargeRes", True)
    received = []

    def fsm() -> None:
        out = pc.intercept("PreChargeRes", {"EVSEPresentVoltage": 220, "Foo": 1})
        received.append(out)

    t = threading.Thread(target=fsm, daemon=True)
    t.start()
    deadline = time.monotonic() + 1.0
    while pc.get_pending() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    pending = pc.get_pending()
    # The override has already merged into the paused params dict.
    assert pending["params"]["EVSEPresentVoltage"] == 380
    assert pending["params"]["Foo"] == 1
    pc.send(pending["params"])
    t.join(timeout=2.0)
    assert received[0]["EVSEPresentVoltage"] == 380


def test_abort_with_override_still_honors_override():
    pc = PauseController()
    pc.set_override("PreChargeRes", {"EVSEPresentVoltage": 380})
    pc.set_pause_enabled("PreChargeRes", True)
    received = []

    def fsm() -> None:
        out = pc.intercept("PreChargeRes", {"EVSEPresentVoltage": 220})
        received.append(out)

    t = threading.Thread(target=fsm, daemon=True)
    t.start()
    deadline = time.monotonic() + 1.0
    while pc.get_pending() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    pc.abort()
    t.join(timeout=2.0)
    # abort honors the override (merged params), not raw defaults
    assert received[0]["EVSEPresentVoltage"] == 380


# --- register_gui_callback ----------------------------------------------


def test_gui_callback_invoked_on_pause():
    pc = PauseController()
    pc.set_pause_enabled("PreChargeReq", True)
    callback_args = []

    def cb(stage, params):
        callback_args.append((stage, dict(params)))

    pc.register_gui_callback(cb)

    def fsm() -> None:
        pc.intercept("PreChargeReq", {"V": 220})

    t = threading.Thread(target=fsm, daemon=True)
    t.start()
    deadline = time.monotonic() + 1.0
    while not callback_args and time.monotonic() < deadline:
        time.sleep(0.005)
    pc.send(None)
    t.join(timeout=2.0)
    assert callback_args == [("PreChargeReq", {"V": 220})]


def test_gui_callback_exception_does_not_deadlock_fsm():
    pc = PauseController()
    pc.set_pause_enabled("PreChargeReq", True)

    def bad_cb(stage, params):
        raise RuntimeError("simulated GUI bug")

    pc.register_gui_callback(bad_cb)

    received = []

    def fsm() -> None:
        out = pc.intercept("PreChargeReq", {"V": 220})
        received.append(out)

    t = threading.Thread(target=fsm, daemon=True)
    t.start()
    # Wait for pause to actually engage
    deadline = time.monotonic() + 1.0
    while pc.get_pending() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    # The FSM is blocked even though cb raised — proves the lock
    # released before invoking the callback.
    assert pc.is_currently_paused() is True
    pc.send({"V": 999})
    t.join(timeout=2.0)
    assert received == [{"V": 999}]


# --- thread-safety smoke test -------------------------------------------


def test_concurrent_overrides_and_intercepts():
    pc = PauseController()
    pc.set_override("PreChargeRes", {"V": 1})

    def writer():
        for i in range(50):
            pc.set_override("PreChargeRes", {"V": i})

    def reader():
        for _ in range(50):
            out = pc.intercept("PreChargeRes", {"V": 0, "x": "y"})
            assert "V" in out

    threads = [threading.Thread(target=writer) for _ in range(2)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    for t in threads:
        assert not t.is_alive()


# --- get_pending / is_currently_paused --------------------------------


def test_get_pending_is_none_when_not_paused():
    pc = PauseController()
    assert pc.get_pending() is None
    assert pc.is_currently_paused() is False


def test_get_pending_returns_copy():
    pc = PauseController()
    pc.set_pause_enabled("PreChargeReq", True)

    def fsm() -> None:
        pc.intercept("PreChargeReq", {"V": 220, "I": 5})

    t = threading.Thread(target=fsm, daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 1.0
        while pc.get_pending() is None and time.monotonic() < deadline:
            time.sleep(0.005)
        pending1 = pc.get_pending()
        # Mutate the returned dict — must not affect the controller's state
        pending1["params"]["V"] = 9999
        pending2 = pc.get_pending()
        assert pending2["params"]["V"] == 220
    finally:
        # Always release the FSM thread, even if assertion fails, so
        # subsequent tests don't run with a leaked blocked thread.
        pc.abort()
        t.join(timeout=2.0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
