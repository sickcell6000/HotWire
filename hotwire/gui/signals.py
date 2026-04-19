"""
Qt signal hub — the only legal way for background threads to talk to the UI.

All emissions from ``QtWorkerThread`` are queued connections (the default
``Qt.AutoConnection`` becomes ``QueuedConnection`` when source and receiver
live in different QThreads). This means:

  * ``emit()`` is non-blocking — it only appends to the receiver's event queue
  * The slot runs on the receiver's thread (main thread for all widgets)
  * No QWidget is touched from the worker thread, so no threading crashes
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class Signals(QObject):
    """Single shared instance passed to both the worker and the widgets."""

    # Trace log line. (level: "INFO"/"ERROR"/"WARNING"/"SUCCESS"/"DEBUG", text)
    trace_emitted = pyqtSignal(str, str)

    # Status-panel field update. (key, value-as-string)
    # Keys include: "evseState", "pevState", "EVCCID", "EVSEPresentVoltage",
    #               "PowerSupplyUTarget", "mode"
    status_changed = pyqtSignal(str, str)

    # Pause hit — FSM is blocked on PauseController, GUI must show a dialog.
    # Payload: (stage_name, merged_default_params)
    pause_hit = pyqtSignal(str, dict)

    # A decoded DIN 70121 message the FSM observed (either incoming Req from
    # peer, or outgoing Res we just built). Payload: (direction, msg_name, decoded_dict)
    # direction ∈ {"rx", "tx"}.
    msg_decoded = pyqtSignal(str, str, dict)

    # Worker lifecycle.
    worker_started = pyqtSignal()
    worker_stopped = pyqtSignal()

    # Checkpoint 13 — attack launcher + session replay.
    # Emitted when an Attack playbook has been installed. (attack_name)
    attack_applied = pyqtSignal(str)
    # Emitted when the operator clicks an event in the replay panel.
    # Main window routes this to the existing ReqResTreeView slot.
    # Payload: (direction, msg_name, decoded_params_dict)
    replay_event_selected = pyqtSignal(str, str, dict)
