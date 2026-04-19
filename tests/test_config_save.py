"""Config writer round-trip tests.

Checkpoint 14 added :func:`hotwire.core.config.save` and
:func:`setConfigValue`. These tests pin the contract:

* Set a value, save, reload → value survives.
* Comments are dropped on save (documented limitation).
* save() without prior load() raises.
"""
from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _prep_fixture_ini(tmp_path: Path, contents: str) -> Path:
    ini = tmp_path / "test_hotwire.ini"
    ini.write_text(contents, encoding="utf-8")
    return ini


def test_setconfig_value_and_save_roundtrips(tmp_path):
    import importlib
    import hotwire.core.config as config_mod
    importlib.reload(config_mod)

    ini = _prep_fixture_ini(tmp_path, "[general]\nmode = EvseMode\n")

    config_mod.load(ini)
    config_mod.setConfigValue("mode", "PevMode")
    config_mod.save()

    # Reload from disk — original ConfigParser is cached, so we reload the module.
    importlib.reload(config_mod)
    config_mod.load(ini)
    assert config_mod.getConfigValue("mode") == "PevMode"


def test_setconfig_bool_is_serialised_correctly(tmp_path):
    import importlib
    import hotwire.core.config as config_mod
    importlib.reload(config_mod)

    ini = _prep_fixture_ini(
        tmp_path, "[general]\nis_simulation_without_modems = false\n",
    )
    config_mod.load(ini)
    config_mod.setConfigValue("is_simulation_without_modems", True)
    config_mod.save()

    importlib.reload(config_mod)
    config_mod.load(ini)
    assert config_mod.getConfigValueBool("is_simulation_without_modems") is True


def test_save_to_alternative_path(tmp_path):
    import importlib
    import hotwire.core.config as config_mod
    importlib.reload(config_mod)

    src = _prep_fixture_ini(tmp_path, "[general]\nmode = EvseMode\n")
    dst = tmp_path / "other.ini"
    config_mod.load(src)
    written = config_mod.save(dst)
    assert written == dst
    assert dst.exists()
    parser = configparser.ConfigParser()
    parser.read(dst, encoding="utf-8")
    assert parser["general"]["mode"] == "EvseMode"


def test_save_without_load_raises(tmp_path):
    import importlib
    import hotwire.core.config as config_mod
    importlib.reload(config_mod)

    with pytest.raises(RuntimeError):
        config_mod.save(tmp_path / "unused.ini")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
