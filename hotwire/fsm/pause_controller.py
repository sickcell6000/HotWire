"""
Pause / Modify / Send controller.

Lets the GUI intercept outbound DIN 70121 / ISO 15118-2 messages
*after* the FSM has assembled default parameters but *before* they are
EXI-encoded and transmitted. The GUI can then edit the fields and
press "Send" to release the FSM.

Workflow:

1. FSM calls ``pc.intercept("PreChargeReq", params)``.
2. If pause is **disabled** for that stage, ``intercept`` returns the
   original params immediately and the FSM proceeds.
3. If pause is **enabled**, the call blocks:
   - The GUI callback is notified with ``(stage, params)``.
   - The GUI displays an editor populated with ``params``.
   - When the user presses Send, the GUI calls ``pc.send(new_params)``.
   - ``intercept`` returns the modified params to the FSM.
4. The FSM continues with (possibly modified) params.

Thread-safe. Exactly one pause can be outstanding at any given time.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional


class PauseController:
    def __init__(self) -> None:
        self._pause_enabled: dict[str, bool] = {}
        self._pending: Optional[dict] = None
        self._release_event = threading.Event()
        self._modified_params: Optional[dict] = None
        self._lock = threading.Lock()
        self._gui_callback: Optional[Callable[[str, dict], None]] = None
        self._abort_flag: bool = False
        # Per-stage "always override" params — applied by intercept() before
        # the pause check. This gives the GUI a legacy "config-provider" mode
        # without forcing pause-and-block for every stage.
        self._overrides: dict[str, dict] = {}

    # ---- GUI-side API ----------------------------------------------

    def set_pause_enabled(self, stage: str, enabled: bool) -> None:
        """GUI: toggle pause for a specific protocol stage.

        Stage names match the keys used by fsmEvse/fsmPev, e.g.
        "SessionSetupReq", "PreChargeReq", "CurrentDemandRes".
        """
        with self._lock:
            self._pause_enabled[stage] = enabled

    def set_all_paused(self, enabled: bool, stages: list[str] | None = None) -> None:
        """Enable/disable pause for every known stage at once."""
        with self._lock:
            if stages is None:
                # Convenience: clear or populate existing map.
                if not enabled:
                    self._pause_enabled.clear()
                    return
                # Without an explicit list we can't populate — caller must
                # pass the stages they care about.
                return
            for stage in stages:
                self._pause_enabled[stage] = enabled

    def is_paused_for(self, stage: str) -> bool:
        with self._lock:
            return self._pause_enabled.get(stage, False)

    def register_gui_callback(self, cb: Callable[[str, dict], None]) -> None:
        """GUI: hook to be notified when an intercept happens."""
        self._gui_callback = cb

    def send(self, modified_params: Optional[dict] = None) -> None:
        """GUI: user pressed Send — release the blocked FSM."""
        with self._lock:
            self._modified_params = modified_params
        self._release_event.set()

    def abort(self) -> None:
        """GUI: cancel the current pause and release the FSM with original params."""
        with self._lock:
            self._abort_flag = True
            self._modified_params = None
        self._release_event.set()

    def is_currently_paused(self) -> bool:
        return self._pending is not None

    def get_pending(self) -> Optional[dict]:
        """GUI: inspect the currently paused message (for display).

        Returns a one-level-deep copy so the caller can mutate the
        ``params`` dict without affecting the controller's stored
        state. Without this, two consecutive calls would return
        snapshots that share the same inner ``params`` reference,
        and a GUI that pre-fills an editor with the first snapshot
        could accidentally re-write the FSM's eventual default.
        """
        with self._lock:
            if not self._pending:
                return None
            snapshot = dict(self._pending)
            params = snapshot.get("params")
            if isinstance(params, dict):
                snapshot["params"] = dict(params)
            return snapshot

    # ---- Override API (legacy "config-provider" parity) -------------

    def set_override(self, stage: str, params: dict) -> None:
        """GUI: install an always-apply override for ``stage``.

        Whenever the FSM calls ``intercept(stage, defaults)``, the override
        dict is merged on top of ``defaults`` before the pause check. The
        FSM returns the merged dict immediately unless pause is also
        enabled, in which case the GUI sees the already-merged dict.
        """
        with self._lock:
            self._overrides[stage] = dict(params)

    def get_override(self, stage: str) -> Optional[dict]:
        with self._lock:
            return dict(self._overrides[stage]) if stage in self._overrides else None

    def clear_override(self, stage: Optional[str] = None) -> None:
        """Remove one override (``stage`` given) or all overrides (``stage=None``)."""
        with self._lock:
            if stage is None:
                self._overrides.clear()
            else:
                self._overrides.pop(stage, None)

    def has_override(self, stage: str) -> bool:
        with self._lock:
            return stage in self._overrides

    # ---- FSM-side API ---------------------------------------------

    def intercept(self, stage: str, params: dict) -> dict:
        """Called by FSM before sending; blocks until GUI releases.

        Flow:
          1. Build ``merged`` = defaults with any registered override applied.
          2. If pause is disabled, return ``merged`` immediately.
          3. If pause is enabled, stash ``merged`` as the pending message and
             block until ``send()`` or ``abort()`` is called.
        """
        with self._lock:
            merged = dict(params)
            override = self._overrides.get(stage)
            if override:
                merged.update(override)
            if not self._pause_enabled.get(stage, False):
                return merged
            self._pending = {"stage": stage, "params": dict(merged)}
            self._modified_params = None
            self._abort_flag = False
            self._release_event.clear()

        # Notify the GUI (outside the lock so it can call back freely).
        if self._gui_callback:
            try:
                self._gui_callback(stage, dict(merged))
            except Exception as e:                                  # noqa: BLE001
                # GUI misbehavior must never deadlock the FSM.
                print(f"[PauseController] GUI callback error: {e}")

        # Block until GUI calls send() or abort().
        self._release_event.wait()

        with self._lock:
            modified = self._modified_params
            aborted = self._abort_flag
            self._pending = None
            self._modified_params = None
            self._abort_flag = False

        # On abort, still honor the override — the user is just skipping the
        # interactive edit, not opting out of the registered override.
        if aborted:
            return merged
        return modified if modified is not None else merged


if __name__ == "__main__":
    import time

    # Demonstration: FSM thread blocks until GUI "presses Send".
    pc = PauseController()
    pc.set_pause_enabled("PreChargeReq", True)

    def fake_fsm() -> None:
        print("[FSM] preparing PreChargeReq...")
        params = {"EVTargetVoltage": 350, "SoC": 30}
        print(f"[FSM] default params: {params}")
        final = pc.intercept("PreChargeReq", params)
        print(f"[FSM] released with params: {final}")

    def fake_gui() -> None:
        time.sleep(0.5)
        pending = pc.get_pending()
        print(f"[GUI] captured pending: {pending}")
        time.sleep(0.5)
        print("[GUI] user edits EVTargetVoltage to 999, pressing Send")
        pc.send({"EVTargetVoltage": 999, "SoC": 30})

    t1 = threading.Thread(target=fake_fsm)
    t2 = threading.Thread(target=fake_gui)
    t1.start(); t2.start()
    t1.join(); t2.join()
    print("Demo complete.")
