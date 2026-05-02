"""Unit tests for MessageObserver wiring in fsmEvse / fsmPev.

These tests use stubs for all external dependencies so the FSM classes
can be constructed without opening sockets or spawning threads. The goal
is to verify that observer callbacks fire with the right (direction,
msg_name, params) shape — which is the contract the GUI layer relies on.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "HOTWIRE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"),
)


class _RecordingObserver:
    """Captures every on_message call for inspection."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def on_message(self, direction, msg_name, params):
        self.events.append((direction, msg_name, params))


def test_observer_sees_tx_from_evse_fsm(monkeypatch):
    """When fsmEvse's _intercept_and_send runs, the observer should see a 'tx' event."""
    from hotwire.fsm import fsm_evse

    # Build an fsmEvse without invoking its TCP binding. We instantiate
    # it via __new__ and set only the attributes that _intercept_and_send
    # touches.
    observer = _RecordingObserver()
    f = fsm_evse.fsmEvse.__new__(fsm_evse.fsmEvse)
    f.callbackAddToTrace = lambda s: None
    f.pause_controller = fsm_evse.PauseController()
    f.message_observer = observer
    f.schemaSelection = "D"

    # Stub the TCP socket to accept transmit().
    class _Tcp:
        def transmit(self, msg):
            return 0
    f.Tcp = _Tcp()

    f._intercept_and_send(
        "SessionSetupRes",
        {"ResponseCode": 1, "EVSEID": "5A5A4445464C54"},
        lambda p: f"EDa_{p['ResponseCode']}_{p['EVSEID']}",
    )

    tx_events = [e for e in observer.events if e[0] == "tx"]
    assert len(tx_events) == 1
    direction, msg_name, params = tx_events[0]
    assert direction == "tx"
    assert msg_name == "SessionSetupRes"
    assert params.get("EVSEID", "").lower() == "5a5a4445464c54"


def test_observer_absent_is_safe(monkeypatch):
    """FSM must work identically with message_observer=None (no crash, no overhead)."""
    from hotwire.fsm import fsm_evse

    f = fsm_evse.fsmEvse.__new__(fsm_evse.fsmEvse)
    f.callbackAddToTrace = lambda s: None
    f.pause_controller = fsm_evse.PauseController()
    f.message_observer = None
    f.schemaSelection = "D"
    class _Tcp:
        def transmit(self, msg):
            return 0
    f.Tcp = _Tcp()

    # Must not raise.
    f._intercept_and_send(
        "SessionStopRes",
        {},
        lambda _p: "EDk",
    )


def test_observer_error_does_not_break_fsm():
    """If the observer raises, the FSM must log and continue."""
    from hotwire.fsm import fsm_evse

    traces: list[str] = []

    class _BrokenObserver:
        def on_message(self, direction, msg_name, params):
            raise RuntimeError("observer blew up")

    f = fsm_evse.fsmEvse.__new__(fsm_evse.fsmEvse)
    f.callbackAddToTrace = lambda s: traces.append(s)
    f.pause_controller = fsm_evse.PauseController()
    f.message_observer = _BrokenObserver()
    f.schemaSelection = "D"
    class _Tcp:
        def transmit(self, msg):
            return 0
    f.Tcp = _Tcp()

    # Must complete without raising.
    f._intercept_and_send("SessionStopRes", {}, lambda _p: "EDk")
    # An error trace should have been logged.
    assert any("observer" in t for t in traces), traces


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            # monkeypatch-accepting tests don't actually use the fixture — pass None.
            if t.__code__.co_argcount == 0:
                t()
            else:
                t(None)
            print(f"[PASS] {t.__name__}")
        except Exception:                                       # noqa: BLE001
            failures += 1
            print(f"[FAIL] {t.__name__}")
            traceback.print_exc()
    sys.exit(0 if failures == 0 else 1)
