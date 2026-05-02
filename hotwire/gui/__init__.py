"""HotWire GUI — PyQt6 interface for EVSE/PEV mode with pause/modify/send.

PyQt6-backed exports (``HotWireMainWindow`` / ``ModeDialog`` / ``run_gui`` /
``Signals``) are loaded **lazily** via :func:`__getattr__` so that sibling
modules that don't need PyQt (e.g. ``hotwire.gui.stage_schema`` is a pure
data module used by fuzz tests) can be imported on hosts where PyQt6 is
absent or its Qt DLLs fail to load — for example a field EVSE host whose
only job is driving the PLC modem.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["HotWireMainWindow", "ModeDialog", "Signals", "run_gui"]


if TYPE_CHECKING:  # pragma: no cover — type checkers only
    from .app import ModeDialog, run_gui  # noqa: F401
    from .main_window import HotWireMainWindow  # noqa: F401
    from .signals import Signals  # noqa: F401


def __getattr__(name: str) -> Any:
    # Defer the actual PyQt6 import until someone asks for one of these
    # names. Importing ``hotwire.gui.stage_schema`` directly still works
    # even when Qt DLLs are missing, because this hook isn't triggered
    # for submodule imports.
    if name in ("ModeDialog", "run_gui"):
        from .app import ModeDialog, run_gui
        return {"ModeDialog": ModeDialog, "run_gui": run_gui}[name]
    if name == "HotWireMainWindow":
        from .main_window import HotWireMainWindow
        return HotWireMainWindow
    if name == "Signals":
        from .signals import Signals
        return Signals
    raise AttributeError(f"module 'hotwire.gui' has no attribute {name!r}")
