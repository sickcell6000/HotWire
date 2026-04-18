"""
HotWire configuration loader.

Reads hotwire.ini (or legacy pyPlc.ini) from the project root or an
explicitly provided path.

Adapted from pyPLC's configmodule.py (GPL-3.0, uhi22).
"""
from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path
from typing import Optional

_config: Optional[configparser.ConfigParser] = None
_config_path: Optional[Path] = None


def _candidate_paths() -> list[Path]:
    """Return the ordered list of ini paths to probe.

    Explicit env var first, then the current working directory, then the
    project ``config/`` folder, then legacy pyPlc.ini for backward compat.
    """
    here = Path(__file__).resolve().parent.parent.parent  # HotWire root
    env_path = os.environ.get("HOTWIRE_CONFIG")
    paths: list[Path] = []
    if env_path:
        paths.append(Path(env_path))
    paths.extend([
        Path.cwd() / "hotwire.ini",
        here / "config" / "hotwire.ini",
        here / "hotwire.ini",
        Path.cwd() / "pyPlc.ini",    # legacy fallback
        here / "pyPlc.ini",
    ])
    return paths


def load(explicit_path: Optional[str | Path] = None) -> configparser.ConfigParser:
    """Load the config exactly once; subsequent calls return cached instance."""
    global _config, _config_path
    if _config is not None and explicit_path is None:
        return _config

    cfg = configparser.ConfigParser()
    paths = [Path(explicit_path)] if explicit_path else _candidate_paths()

    for p in paths:
        if p.is_file():
            cfg.read(p, encoding="utf-8")
            _config = cfg
            _config_path = p
            return cfg

    print("ERROR: could not find hotwire.ini. Searched:")
    for p in paths:
        print(f"  - {p}")
    print("Copy config/hotwire.ini to your working directory and edit it.")
    sys.exit(1)


def getConfigValue(key: str) -> str:
    """Get a string config value from the [general] section."""
    cfg = load()
    try:
        return cfg["general"][key]
    except KeyError:
        print(f"ERROR: config key '{key}' missing from [general] section of {_config_path}")
        sys.exit(1)


def getConfigValueBool(key: str) -> bool:
    """Get a boolean config value from the [general] section."""
    cfg = load()
    try:
        return cfg.getboolean("general", key)
    except (KeyError, ValueError):
        print(f"ERROR: boolean config key '{key}' missing or invalid in {_config_path}")
        sys.exit(1)


if __name__ == "__main__":
    print("Testing hotwire.core.config ...")
    cfg = load()
    print(f"Config loaded from: {_config_path}")
    print(f"Sections: {cfg.sections()}")
    print(f"mode = {getConfigValue('mode')}")
    print(f"is_simulation_without_modems = {getConfigValueBool('is_simulation_without_modems')}")
