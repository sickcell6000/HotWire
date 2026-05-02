"""HotWire GUI entry point.

Usage:

    python scripts/run_gui.py                     # mode dialog, simulation
    python scripts/run_gui.py --mode evse --sim   # skip dialog, force sim
    python scripts/run_gui.py --mode pev --hw     # real hardware, PEV role
    python scripts/run_gui.py --config path.ini   # override config file
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make HotWire importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.core.config import load as load_config  # noqa: E402
from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE  # noqa: E402
from hotwire.gui.app import run_gui  # noqa: E402


_MODE_MAP = {"evse": C_EVSE_MODE, "pev": C_PEV_MODE}


def main() -> int:
    parser = argparse.ArgumentParser(description="HotWire GUI (PyQt6)")
    parser.add_argument(
        "--mode",
        choices=["evse", "pev"],
        help="Skip the startup mode dialog and start directly.",
    )
    sim_group = parser.add_mutually_exclusive_group()
    sim_group.add_argument(
        "--sim", action="store_true", default=True,
        help="Use pure-software simulation (default).",
    )
    sim_group.add_argument(
        "--hw", action="store_true",
        help="Use real PLC hardware (pypcap required).",
    )
    parser.add_argument(
        "--config",
        help="Path to hotwire.ini (overrides HOTWIRE_CONFIG env var).",
    )
    args = parser.parse_args()

    if args.config:
        os.environ["HOTWIRE_CONFIG"] = args.config
    elif "HOTWIRE_CONFIG" not in os.environ:
        os.environ["HOTWIRE_CONFIG"] = str(
            Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"
        )

    cfg = load_config()

    # GUI mode owns the worker lifecycle via the Stop button — we must
    # never let connMgr / addressManager call ``sys.exit()`` from inside
    # the worker thread, because that wedges the GUI (the worker thread
    # dies but the main thread's Start button is still disabled until
    # a ``worker_stopped`` signal that never fires). Hard-override these
    # two switches regardless of what hotwire.ini says.
    if cfg.has_section("general"):
        cfg.set("general", "exit_on_session_end", "False")
        cfg.set("general", "exit_if_no_local_link_address_is_found", "False")

    is_sim = not args.hw
    mode = _MODE_MAP.get(args.mode) if args.mode else None
    return run_gui(mode=mode, is_simulation=is_sim)


if __name__ == "__main__":
    sys.exit(main())
