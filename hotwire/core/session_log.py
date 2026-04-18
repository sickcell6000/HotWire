"""
Session logger — persist every decoded Req/Res to JSONL for post-hoc analysis.

Each line of the output file is one JSON object with:

  * ``timestamp``: ISO-8601 local time with microsecond precision
  * ``direction``: "rx" (received from peer) | "tx" (sent to peer)
  * ``msg_name``: DIN 70121 message name, e.g. "SessionSetupReq"
  * ``mode``: "EVSE" | "PEV" — which side we were running
  * ``params``: the full decoded parameter dict from OpenV2G

The logger satisfies the paper's "log all received and transmitted messages
to persistent storage" requirement. JSONL is preferred over XML because
every consumer (pandas, jq, Wireshark's JSON importer) speaks it natively,
and tailing-while-running is trivial.

Usage::

    from hotwire.core.session_log import SessionLogger
    logger = SessionLogger("sessions/evse_2026-04-18.jsonl", mode="EVSE")
    worker = HotWireWorker(..., message_observer=logger)
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
from pathlib import Path
from typing import Any, Optional


class SessionLogger:
    """Implements :class:`MessageObserver` by appending JSON lines to a file.

    File writes are guarded by a lock so concurrent rx/tx from the FSM
    thread and (future) other observers can't interleave mid-line. The
    file is opened once in append mode and flushed after every line so a
    ``kill -9`` during a session still leaves a coherent log.
    """

    def __init__(self, path: str | Path, mode: str = "") -> None:
        self._path = Path(path)
        self._mode = mode
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Append mode so multiple sessions into one file become a single
        # JSONL stream ordered by wall clock.
        self._fh = self._path.open("a", encoding="utf-8", buffering=1)

    def on_message(
        self,
        direction: str,
        msg_name: str,
        params: dict[str, Any],
    ) -> None:
        record = {
            "timestamp": _dt.datetime.now().isoformat(timespec="microseconds"),
            "direction": direction,
            "msg_name": msg_name,
            "mode": self._mode,
            "params": params,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._fh.write(line)
            self._fh.write("\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except ValueError:
                pass

    def __enter__(self) -> "SessionLogger":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class TeeObserver:
    """Fan-out helper — forwards ``on_message`` to every observer in order.

    Useful when you want both the Qt-signal observer (for the GUI tree
    view) AND a :class:`SessionLogger` active at the same time. The
    observers run synchronously on the FSM thread in the order given.
    """

    def __init__(self, *observers) -> None:
        self._observers = observers

    def on_message(self, direction, msg_name, params) -> None:
        for obs in self._observers:
            try:
                obs.on_message(direction, msg_name, params)
            except Exception:                                   # noqa: BLE001
                # Don't let one buggy observer break the rest.
                pass
