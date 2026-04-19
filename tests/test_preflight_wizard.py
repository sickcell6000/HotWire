"""pytest-qt smoke tests for PreflightWizard.

These skip the actual runner thread (it drags 2+ seconds) by calling
the wizard's internal page directly where possible. The full end-to-end
wizard flow is validated by the GUI manual-smoke checklist.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini"))

pytest.importorskip("PyQt6")

from hotwire.core.config import load as load_config              # noqa: E402

load_config()

from hotwire.gui.widgets.preflight_wizard import (                # noqa: E402
    PreflightWizard,
    _build_remediation_card,
)
from hotwire.preflight.checks import CheckResult, CheckStatus     # noqa: E402


def test_wizard_constructs_with_three_pages(qtbot):
    wizard = PreflightWizard()
    qtbot.addWidget(wizard)
    # addPage(...) 3 times → pageIds should yield 3 indices.
    assert len(wizard.pageIds()) == 3


def test_remediation_card_renders(qtbot):
    result = CheckResult(
        name="fake", status=CheckStatus.FAIL,
        observed="x", expected="y",
        remediation="sudo fix-thing",
    )
    card = _build_remediation_card(result)
    qtbot.addWidget(card)
    # The card contains a QLineEdit with the remediation text.
    from PyQt6.QtWidgets import QLineEdit
    edits = card.findChildren(QLineEdit)
    assert any(e.text() == "sudo fix-thing" for e in edits)


def test_warn_card_gets_yellow_styling(qtbot):
    result = CheckResult(
        name="fake", status=CheckStatus.WARN,
        observed="x", expected="y",
        remediation="maybe fix",
    )
    card = _build_remediation_card(result)
    qtbot.addWidget(card)
    ss = card.styleSheet()
    assert "FFF8E1" in ss.upper() or "FFB300" in ss.upper()       # yellow


def test_fail_card_gets_red_styling(qtbot):
    result = CheckResult(
        name="fake", status=CheckStatus.FAIL,
        observed="x", expected="y",
        remediation="must fix",
    )
    card = _build_remediation_card(result)
    qtbot.addWidget(card)
    ss = card.styleSheet()
    assert "FFEBEE" in ss.upper() or "C62828" in ss.upper()       # red


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
