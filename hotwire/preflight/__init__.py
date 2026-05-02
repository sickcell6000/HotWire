"""HotWire hardware preflight — 20+ checks to run before touching real EV hardware.

Two entry points share this package:

* ``scripts/hw_check/phase0_hw.py`` — CLI, produces REPORT.md + JSONL
* ``hotwire/gui/widgets/preflight_wizard.py`` — PyQt6 QWizard, interactive

Design:

* Each check is a small function that returns a :class:`CheckResult`
  with observed/expected/remediation strings.
* Checks are registered via the ``@register_check`` decorator so the
  list is discovered automatically.
* The :class:`PreflightRunner` iterates the registry, filters by
  current platform, and emits results one-by-one (so the wizard can
  update its UI progressively).
"""

from .checks import Check, CheckResult, CheckStatus, CHECKS
from .runner import PreflightRunner

__all__ = [
    "Check",
    "CheckResult",
    "CheckStatus",
    "CHECKS",
    "PreflightRunner",
]
