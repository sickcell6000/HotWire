"""pytest-qt tests for AttackLauncherDialog.

Verifies:
  * The dropdown auto-filters playbooks by mode.
  * Dataclass fields turn into form widgets.
  * Apply instantiates the Attack, calls apply(), emits signal.
  * Invalid input → QMessageBox + no install.
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

from PyQt6.QtWidgets import QLineEdit, QSpinBox                  # noqa: E402

from hotwire.core.config import load as load_config              # noqa: E402

load_config()

from hotwire.attacks import AutochargeImpersonation, ForcedDischarge  # noqa: E402
from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE           # noqa: E402
from hotwire.fsm.pause_controller import PauseController         # noqa: E402
from hotwire.gui.widgets.attack_launcher import (                # noqa: E402
    AVAILABLE_ATTACKS,
    AttackLauncherDialog,
)


def test_pev_mode_filters_to_pev_attacks(qtbot):
    pc = PauseController()
    dlg = AttackLauncherDialog(mode=C_PEV_MODE, pause_controller=pc)
    qtbot.addWidget(dlg)
    # AutochargeImpersonation's mode is PEV; ForcedDischarge's is EVSE.
    names = [dlg._combo.itemText(i) for i in range(dlg._combo.count())]
    assert "AutochargeImpersonation" in names
    assert "ForcedDischarge" not in names


def test_evse_mode_filters_to_evse_attacks(qtbot):
    pc = PauseController()
    dlg = AttackLauncherDialog(mode=C_EVSE_MODE, pause_controller=pc)
    qtbot.addWidget(dlg)
    names = [dlg._combo.itemText(i) for i in range(dlg._combo.count())]
    assert "ForcedDischarge" in names
    assert "AutochargeImpersonation" not in names


def test_form_is_built_from_dataclass_fields(qtbot):
    pc = PauseController()
    dlg = AttackLauncherDialog(mode=C_PEV_MODE, pause_controller=pc)
    qtbot.addWidget(dlg)
    # Autocharge has one non-base field: evccid (str).
    assert "evccid" in dlg._field_widgets
    assert isinstance(dlg._field_widgets["evccid"], QLineEdit)


def test_forced_discharge_has_int_fields(qtbot):
    pc = PauseController()
    dlg = AttackLauncherDialog(mode=C_EVSE_MODE, pause_controller=pc)
    qtbot.addWidget(dlg)
    assert "voltage" in dlg._field_widgets
    assert "current" in dlg._field_widgets
    assert isinstance(dlg._field_widgets["voltage"], QSpinBox)
    assert isinstance(dlg._field_widgets["current"], QSpinBox)


def test_apply_installs_override_on_pause_controller(qtbot):
    pc = PauseController()
    dlg = AttackLauncherDialog(mode=C_PEV_MODE, pause_controller=pc)
    qtbot.addWidget(dlg)

    # Fill in a valid EVCCID.
    dlg._field_widgets["evccid"].setText("deadbeef0000")

    with qtbot.waitSignal(dlg.attack_launched, timeout=1000) as blocker:
        dlg._on_apply()
    assert "Autocharge" in blocker.args[0]

    # Override installed on the pause controller.
    ov = pc.get_override("SessionSetupReq")
    assert ov == {"EVCCID": "deadbeef0000"}


def test_apply_rejects_invalid_evccid(qtbot, monkeypatch):
    pc = PauseController()
    dlg = AttackLauncherDialog(mode=C_PEV_MODE, pause_controller=pc)
    qtbot.addWidget(dlg)
    # Evade the modal QMessageBox pop-up in headless test.
    from hotwire.gui.widgets import attack_launcher as mod
    warnings: list[str] = []
    monkeypatch.setattr(
        mod.QMessageBox, "warning",
        lambda *a, **kw: warnings.append(a[2] if len(a) > 2 else ""),
    )

    dlg._field_widgets["evccid"].setText("not-hex")
    dlg._on_apply()

    # Nothing was installed.
    assert pc.get_override("SessionSetupReq") is None
    # User was warned.
    assert any("EVCCID" in msg for msg in warnings)


def test_clear_all_removes_existing_overrides(qtbot, monkeypatch):
    pc = PauseController()
    pc.set_override("SessionSetupReq", {"EVCCID": "aabbccddeeff"})
    pc.set_override("PreChargeRes", {"EVSEPresentVoltage": 999})

    dlg = AttackLauncherDialog(mode=C_EVSE_MODE, pause_controller=pc)
    qtbot.addWidget(dlg)

    # Suppress the "overrides cleared" info dialog.
    from hotwire.gui.widgets import attack_launcher as mod
    monkeypatch.setattr(
        mod.QMessageBox, "information", lambda *a, **kw: None
    )

    with qtbot.waitSignal(dlg.attack_launched, timeout=1000):
        dlg._on_clear_all()

    assert pc.get_override("SessionSetupReq") is None
    assert pc.get_override("PreChargeRes") is None


def test_registry_matches_attacks_module():
    """If someone adds an Attack to hotwire.attacks, they should
    remember to append it to AVAILABLE_ATTACKS. This test is a reminder."""
    # Known-good: both playbooks shipped at Checkpoint 13.
    assert AutochargeImpersonation in AVAILABLE_ATTACKS
    assert ForcedDischarge in AVAILABLE_ATTACKS


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
