"""
Base class for HotWire attack playbooks.

An :class:`Attack` is a declarative description of how to reshape the FSM's
outbound messages. It doesn't spawn threads, TCP sockets, or GUIs — the
caller does that. All the Attack does is install the right
:meth:`PauseController.set_override` calls for its mode.

Typical flow in an entry-point script::

    from hotwire.attacks import AutochargeImpersonation
    from hotwire.gui.app import run_gui

    attack = AutochargeImpersonation(evccid="d83add22f182")
    print(attack.describe())
    # run_gui will construct the pause_controller; we pass it in after.
    run_gui(mode=attack.mode, is_simulation=True, attack=attack)

(The ``run_gui(..., attack=...)`` form is added in the same changeset as
this module; see ``hotwire/gui/app.py``.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..fsm.pause_controller import PauseController


@dataclass
class Attack:
    """A named collection of per-stage PauseController overrides.

    Subclasses set ``name``, ``mode``, ``description`` and populate
    ``overrides`` in ``__post_init__``. Callers apply the attack by passing
    a live PauseController to :meth:`apply`.
    """

    # Populated by subclasses.
    name: str = ""
    mode: int = 0
    description: str = ""
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)

    def apply(self, pause_controller: PauseController) -> None:
        """Install every override onto ``pause_controller``."""
        for stage, params in self.overrides.items():
            pause_controller.set_override(stage, params)

    def clear(self, pause_controller: PauseController) -> None:
        """Reverse :meth:`apply` — remove every stage this attack touched."""
        for stage in self.overrides:
            pause_controller.clear_override(stage)

    def describe(self) -> str:
        """Human-readable summary for logs and CLI banners."""
        lines = [f"=== Attack: {self.name} ===",
                 f"  Mode: {'EVSE' if self.mode == 2 else 'PEV' if self.mode == 1 else '?'}",
                 f"  {self.description}",
                 "  Overrides:"]
        for stage, params in self.overrides.items():
            lines.append(f"    {stage}:")
            for k, v in params.items():
                lines.append(f"      {k} = {v}")
        return "\n".join(lines)
