"""pytest-qt tests for ConfigEditor."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PyQt6")


def _reload_config_with(tmp_path, contents):
    ini = tmp_path / "hotwire.ini"
    ini.write_text(contents, encoding="utf-8")
    os.environ["HOTWIRE_CONFIG"] = str(ini)
    import hotwire.core.config as config_mod
    importlib.reload(config_mod)
    config_mod.load(ini)
    return ini


def test_editor_loads_values(qtbot, tmp_path):
    _reload_config_with(
        tmp_path,
        "[general]\n"
        "mode = EvseMode\n"
        "is_simulation_without_modems = false\n"
        "tcp_port_alternative = 57122\n"
        "protocol_preference = prefer_din\n",
    )
    from hotwire.gui.widgets.config_editor import ConfigEditor
    editor = ConfigEditor()
    qtbot.addWidget(editor)

    # mode is enum — should be a QComboBox.
    from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox
    assert isinstance(editor._widgets["mode"], QComboBox)          # noqa: SLF001
    assert editor._widgets["mode"].currentText() == "EvseMode"     # noqa: SLF001

    # Bool → QCheckBox.
    assert isinstance(                                             # noqa: SLF001
        editor._widgets["is_simulation_without_modems"], QCheckBox
    )

    # Integer → QSpinBox.
    assert isinstance(                                             # noqa: SLF001
        editor._widgets["tcp_port_alternative"], QSpinBox
    )


def test_editor_collect_values(qtbot, tmp_path):
    _reload_config_with(
        tmp_path,
        "[general]\n"
        "mode = EvseMode\n"
        "is_simulation_without_modems = false\n"
        "tcp_port_alternative = 57122\n"
        "protocol_preference = prefer_din\n",
    )
    from hotwire.gui.widgets.config_editor import ConfigEditor
    editor = ConfigEditor()
    qtbot.addWidget(editor)

    editor._widgets["mode"].setCurrentText("PevMode")              # noqa: SLF001
    collected = editor._collect_values()                           # noqa: SLF001
    assert collected["mode"] == "PevMode"
    assert collected["is_simulation_without_modems"] == "false"


def test_editor_save_round_trip(qtbot, tmp_path, monkeypatch):
    ini = _reload_config_with(
        tmp_path,
        "[general]\n"
        "mode = EvseMode\n"
        "protocol_preference = prefer_din\n",
    )
    from hotwire.gui.widgets.config_editor import ConfigEditor
    editor = ConfigEditor()
    qtbot.addWidget(editor)

    # Evade the "overwriting comments" confirm dialog.
    from hotwire.gui.widgets import config_editor as cem
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        cem.QMessageBox, "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )

    editor._widgets["mode"].setCurrentText("PevMode")              # noqa: SLF001

    with qtbot.waitSignal(editor.config_saved, timeout=2000) as _b:
        editor._on_save()                                          # noqa: SLF001

    # Read back from disk.
    import configparser
    cp = configparser.ConfigParser()
    cp.read(ini, encoding="utf-8")
    assert cp["general"]["mode"] == "PevMode"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
