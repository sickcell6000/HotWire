"""
Optional observer hook for FSM message traffic.

Decouples the state machines (pure Python, Qt-free) from the GUI layer
(PyQt6). Any component that wants to see every decoded Req/Res pair can
implement the :class:`MessageObserver` protocol and hand an instance to
``fsmEvse`` / ``fsmPev`` at construction time.

The observer's ``on_message`` is called synchronously from the FSM
thread — keep it fast and non-blocking. GUI bridges should push the
payload onto a queued Qt signal and return immediately.
"""
from __future__ import annotations

from typing import Any, Protocol


class MessageObserver(Protocol):
    """Receives a notification for every DIN 70121 message the FSM handles."""

    def on_message(
        self,
        direction: str,   # "rx" = incoming from peer, "tx" = outgoing we sent
        msg_name: str,    # e.g. "SessionSetupReq", "PreChargeRes"
        params: dict[str, Any],
    ) -> None:
        ...
