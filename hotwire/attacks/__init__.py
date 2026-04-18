"""HotWire attack playbooks — reusable attack scenarios built on top of PauseController.

Each playbook is a thin subclass of :class:`Attack` that names the FSM mode
it needs (EVSE or PEV) and, when activated against a running worker,
installs a fixed set of :meth:`PauseController.set_override` calls so the
FSM transmits the attacker-shaped protocol messages.

Playbooks are deliberately pure-data: they do not spawn their own worker,
FSM, or GUI — the ``scripts/attacks/*.py`` entry points handle that. This
separation keeps the attack definitions testable without any Qt or network
setup, and lets the same playbook be driven from:

  * the CLI one-shot scripts (``scripts/attacks/...``)
  * the GUI's Attacks menu (future work)
  * a pytest integration test
"""

from .base import Attack
from .autocharge_impersonation import AutochargeImpersonation
from .forced_discharge import ForcedDischarge

__all__ = ["Attack", "AutochargeImpersonation", "ForcedDischarge"]
