"""
Phase 4 V2G with attack — applies a named Attack playbook before
constructing the worker, so we can exercise PauseController.set_override
on real hardware in either role.

Wraps :func:`scripts.hw_check.phase4_v2g.phase4_v2g` with an optional
``--attack`` flag that selects an attack from ``hotwire.attacks``:

  --attack a1 --evccid AABBCCDDEEFF      → AutochargeImpersonation
  --attack a2 --voltage 380 --current 5  → ForcedDischarge

This is purely a test harness — production attack runs go through
``record_sustained.py`` (A2) or the GUI's attack launcher.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))
sys.path.insert(0, str(_THIS.parent.parent.parent))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--role", choices=("pev", "evse"), required=True)
    parser.add_argument("--budget", type=float, default=60.0)
    parser.add_argument("--min-cd", type=int, default=5)
    parser.add_argument(
        "--attack", choices=("a1", "a2", "none"), default="none",
        help="Apply an attack before the worker starts.",
    )
    parser.add_argument(
        "--evccid", default="",
        help="A1 only: 12-hex EVCCID to spoof in SessionSetupReq.",
    )
    parser.add_argument(
        "--voltage", type=int, default=380,
        help="A2 only: fabricated EVSEPresentVoltage (V).",
    )
    parser.add_argument(
        "--current", type=int, default=5,
        help="A2 only: fabricated EVSEPresentCurrent (A).",
    )
    args = parser.parse_args(argv)

    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController
    from _runner import RunContext, print_banner, print_result   # type: ignore
    from phase4_v2g import phase4_v2g                             # type: ignore

    pause_controller = PauseController()
    attack_label = "none"

    if args.attack == "a1":
        from hotwire.attacks import AutochargeImpersonation
        if not args.evccid:
            parser.error("--attack a1 requires --evccid")
        atk = AutochargeImpersonation(evccid=args.evccid)
        atk.apply(pause_controller)
        attack_label = f"A1(evccid={args.evccid})"
        print(atk.describe())
    elif args.attack == "a2":
        from hotwire.attacks import ForcedDischarge
        atk = ForcedDischarge(voltage=args.voltage, current=args.current)
        atk.apply(pause_controller)
        attack_label = f"A2(V={args.voltage},I={args.current})"
        print(atk.describe())

    print_banner(
        f"Phase 4 — DIN session ({args.role}) [attack={attack_label}]"
    )
    ctx = RunContext.create_standalone(interface=args.interface)
    try:
        result = phase4_v2g(
            ctx,
            role=args.role,
            budget_s=args.budget,
            min_current_demand=args.min_cd,
            pause_controller=pause_controller,
        )
    finally:
        ctx.close()
    print_result(result)
    return 0 if result.status.name == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
