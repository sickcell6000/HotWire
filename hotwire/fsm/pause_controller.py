"""
Per-stage parameter override controller.

The FSM hands its default response/request params through
``intercept(stage, params)`` immediately before EXI encoding. If the
operator (or an attack script) has installed an override for that stage
via :meth:`set_override`, the override dict is merged on top of the
defaults and the merged result is returned. Without an override the
defaults pass through unchanged.

This is what the paper's attack scenarios use to inject spoofed values
(``EVCCID``, ``EVSEPresentVoltage``, ``EVTargetVoltage`` etc.) without
modifying the FSM source.

The class is named ``PauseController`` for backward compatibility with
the wider codebase and the legacy GUI; an earlier version also offered
an interactive "pause-and-edit" mode that blocked the FSM thread until
the GUI released it. That feature is no longer used by HotWire and was
removed because it is incompatible with DIN 70121 §9.6 spec timeouts on
real vehicles. All pause/release/abort APIs are gone.
"""
from __future__ import annotations

import threading
from typing import Optional


class PauseController:
    """Thread-safe, stage-keyed parameter override store.

    Despite the name (kept for compat), this class no longer pauses the
    FSM. ``intercept()`` returns merged (defaults + override) params
    immediately without blocking.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---- Override API ----------------------------------------------

    def set_override(self, stage: str, params: dict) -> None:
        """Install an always-apply override for ``stage``.

        Whenever the FSM calls ``intercept(stage, defaults)``, this
        override dict is merged on top of ``defaults`` and the merged
        result is returned to the FSM.
        """
        with self._lock:
            self._overrides[stage] = dict(params)

    def get_override(self, stage: str) -> Optional[dict]:
        with self._lock:
            return dict(self._overrides[stage]) if stage in self._overrides else None

    def clear_override(self, stage: Optional[str] = None) -> None:
        """Remove one override (``stage`` given) or all (``stage=None``)."""
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
        """Called by FSM before EXI-encoding the outbound message.

        Returns ``defaults`` with any installed override merged on top.
        Never blocks.

        **Empty-string filter** — defense in depth against the SessionID
        poisoning bug from commit 66fb6d9. The GUI's
        ``StageConfigPanel.get_values`` already drops empty-string
        values before they ever reach this controller; this second
        filter catches the same class of bug from any other entry point
        (scripted ``set_override({"SessionID": ""})`` calls, future
        Attack subclasses with empty defaults, etc.). Empty string in
        an override means "operator did not provide a value, please
        keep the FSM's default" — never "operator wants this field
        literally empty on the wire", which would shift OpenV2G's
        positional command-line args and produce malformed EXI.

        Numeric / boolean override values pass through unchanged so
        intentional ``False`` / ``0`` overrides still work.
        """
        with self._lock:
            merged = dict(params)
            override = self._overrides.get(stage)
            if override:
                cleaned = {
                    k: v for k, v in override.items()
                    if not (isinstance(v, str) and v == "")
                }
                merged.update(cleaned)
            return merged
