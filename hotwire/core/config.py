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


def setConfigValue(key: str, value: str | bool | int) -> None:
    """Mutate the in-memory ConfigParser.

    Call :func:`save` afterwards to persist. Used by the GUI config
    editor (Checkpoint 14). Raises if ``load()`` hasn't been called yet.
    """
    cfg = load()
    if "general" not in cfg.sections():
        cfg.add_section("general")
    if isinstance(value, bool):
        cfg.set("general", key, "true" if value else "false")
    else:
        cfg.set("general", key, str(value))


def save(path: Optional[str | Path] = None) -> Path:
    """Persist the current in-memory config to disk.

    Returns the path that was written. If ``path`` is omitted, the file
    previously loaded (via :func:`load`) is overwritten.

    Known limitation: :mod:`configparser` discards comments on write,
    so the output loses the human-friendly commentary from
    ``config/hotwire.ini``. Callers wanting to preserve comments
    should keep a backup and only use this for runtime-editable keys.
    """
    global _config, _config_path
    if _config is None:
        raise RuntimeError(
            "config.save() called before config.load(); no config in memory"
        )
    target = Path(path) if path else _config_path
    if target is None:
        raise RuntimeError("no config path known to save to")
    with open(target, "w", encoding="utf-8") as fh:
        _config.write(fh)
    if path is None:
        # Update cached path if we just wrote back to original.
        _config_path = target
    return target


if __name__ == "__main__":
    print("Testing hotwire.core.config ...")
    cfg = load()
    print(f"Config loaded from: {_config_path}")
    print(f"Sections: {cfg.sections()}")
    print(f"mode = {getConfigValue('mode')}")
    print(f"is_simulation_without_modems = {getConfigValueBool('is_simulation_without_modems')}")
