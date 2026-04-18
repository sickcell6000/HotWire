"""
Run every hardware-validation phase in sequence.

Creates a single ``runs/<timestamp>/`` directory and drives the four
phases through one shared :class:`RunContext` so every artifact —
REPORT.md, session.jsonl, per-phase pcaps, config snapshot — lands in
the same place.

Typical invocations:

* ``python scripts/hw_check/run_all.py``
    dev-box dry run: each hardware-dependent phase reports SKIP,
    producing a tiny sanity report plus a config snapshot.

* ``python scripts/hw_check/run_all.py -i eth0 --role pev``
    full PEV-side validation against whatever's on ``eth0``.

* ``python scripts/hw_check/run_all.py -i eth0 --role pev --skip 4``
    skip the end-to-end session — useful while bring-up is still
    flaky on earlier layers.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))
sys.path.insert(0, str(_THIS.parent.parent.parent))

from _runner import (  # noqa: E402
    RunContext,
    Status,
    print_banner,
    print_result,
    run_phase,
)
from phase0_env import phase0_env  # noqa: E402
from phase1_link import phase1_link  # noqa: E402
from phase2_slac import phase2_slac  # noqa: E402
from phase3_sdp import phase3_sdp  # noqa: E402
from phase4_v2g import phase4_v2g  # noqa: E402


PHASE_BUILDERS = [
    # (number, name, build_kwargs_callable)
    (0, "phase0_env", lambda args: {}),
    (1, "phase1_link", lambda args: dict(
        duration_s=args.link_duration,
        min_frames=args.link_min_frames,
    )),
    (2, "phase2_slac", lambda args: dict(
        role=args.role, budget_s=args.slac_budget,
    )),
    (3, "phase3_sdp", lambda args: dict(
        role=args.role, scope_id=args.scope_id,
        budget_s=args.sdp_budget,
        secc_ip=args.secc_ip, secc_port=args.secc_port,
    )),
    (4, "phase4_v2g", lambda args: dict(
        role=args.role, budget_s=args.v2g_budget,
        min_current_demand=args.min_cd,
    )),
]

PHASE_FUNC = {
    "phase0_env": phase0_env,
    "phase1_link": phase1_link,
    "phase2_slac": phase2_slac,
    "phase3_sdp": phase3_sdp,
    "phase4_v2g": phase4_v2g,
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    print_banner("HotWire hardware-readiness run")

    run_root = Path(args.run_root) if args.run_root else None
    ctx = (
        RunContext.create(run_root, interface=args.interface,
                          extra_config={"role": args.role})
        if run_root
        else RunContext.create_standalone(interface=args.interface)
    )
    # Patch the context's config with the role so REPORT.md shows it.
    ctx.config["role"] = args.role
    ctx.config["args"] = vars(args)

    skip = set(args.skip or [])
    only = set(args.only) if args.only else None

    any_fail = False
    for num, name, build_kwargs in PHASE_BUILDERS:
        if only is not None and num not in only:
            continue
        if num in skip:
            # Record a SKIP so the report still shows the phase existed.
            ctx.report.add(_skip_result(name, "skipped by --skip flag"))
            continue
        kwargs = build_kwargs(args)
        result = run_phase(ctx, name, PHASE_FUNC[name], **kwargs)
        print_result(result)
        if result.status in (Status.FAIL, Status.ERROR):
            any_fail = True
            if args.halt_on_fail:
                ctx.log.event(
                    kind="run.halt", reason=f"{name} failed",
                )
                break

    ctx.log.event(kind="run.summary", any_failure=any_fail)
    ctx.close()

    print(f"\nArtifacts: {ctx.run_dir}")
    print(f"Report   : {ctx.report.path}")
    return 1 if any_fail else 0


# --- helpers ----------------------------------------------------------


def _skip_result(name: str, reason: str):
    # Local import to avoid a circular at module load time.
    from _runner import PhaseResult, Status as _S
    return PhaseResult(name=name, status=_S.SKIP, summary=reason)


def _parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--interface", "-i", default=None,
                   help="Interface to run against. Omit for a dev-box run.")
    p.add_argument("--role", default="pev", choices=("pev", "evse"),
                   help="Role HotWire plays in phases 2-4.")
    p.add_argument("--run-root", default=None,
                   help="Directory under which to create the run folder. "
                        "Defaults to <repo>/runs.")
    p.add_argument("--skip", type=_intlist, default=[],
                   help="Comma-separated phase numbers to skip (e.g. 2,3).")
    p.add_argument("--only", type=_intlist, default=None,
                   help="If set, run only these phase numbers.")
    p.add_argument("--halt-on-fail", action="store_true",
                   help="Stop on the first FAIL/ERROR.")

    # Per-phase knobs
    p.add_argument("--link-duration", type=float, default=10.0,
                   help="Phase 1 sniff window in seconds.")
    p.add_argument("--link-min-frames", type=int, default=1,
                   help="Phase 1 PASS threshold.")

    p.add_argument("--slac-budget", type=float, default=20.0,
                   help="Phase 2 total budget in seconds.")

    p.add_argument("--sdp-budget", type=float, default=8.0,
                   help="Phase 3 total budget in seconds.")
    p.add_argument("--scope-id", type=int, default=0,
                   help="IPv6 interface scope index (0 = OS default).")
    p.add_argument("--secc-ip", default=None,
                   help="EVSE side only: IPv6 to advertise via SDP.")
    p.add_argument("--secc-port", type=int, default=15118,
                   help="EVSE side only: TCP port to advertise via SDP.")

    p.add_argument("--v2g-budget", type=float, default=60.0,
                   help="Phase 4 session budget in seconds.")
    p.add_argument("--min-cd", type=int, default=5,
                   help="Phase 4 PEV: CurrentDemandRes count for PASS.")
    return p.parse_args(argv)


def _intlist(text: str) -> list[int]:
    if not text:
        return []
    return [int(x) for x in text.split(",") if x.strip()]


if __name__ == "__main__":
    sys.exit(main())
