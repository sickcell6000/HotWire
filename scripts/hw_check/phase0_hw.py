"""
Phase 0 (hardware) — extended 20-item preflight.

Runs the preflight check registry (:mod:`hotwire.preflight.checks`)
against the host and selected interface. Differs from ``phase0_env``:

* ``phase0_env`` is **dev-box sanity** — can we even import + encode?
* ``phase0_hw`` is **hardware-readiness** — MTU, carrier, IPv6
  link-local, system clock, architecture, kernel version, etc.

Call the two in sequence for a full bring-up check. Either can run
standalone via its own ``main()``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))
sys.path.insert(0, str(_THIS.parent.parent.parent))

from _runner import (  # noqa: E402
    PhaseResult,
    RunContext,
    Status,
    print_banner,
    print_result,
    run_phase,
)
from hotwire.preflight import PreflightRunner  # noqa: E402
from hotwire.preflight.checks import CheckStatus  # noqa: E402
from hotwire.preflight.runner import format_text  # noqa: E402


def phase0_hw(ctx: RunContext) -> PhaseResult:
    iface = ctx.interface
    runner = PreflightRunner(interface=iface, stop_on_fail=False)

    results = []
    for r in runner.iter_results():
        results.append(r)
        ctx.log.event(
            kind="phase0_hw.check",
            name=r.name, status=r.status.value,
            observed=r.observed, expected=r.expected,
            remediation=r.remediation, elapsed_ms=r.elapsed_ms,
        )

    # --- Build phase metrics + status
    metrics: dict[str, object] = {
        "checks_total": len(results),
        "checks_pass": sum(1 for r in results if r.status == CheckStatus.PASS),
        "checks_fail": sum(1 for r in results if r.status == CheckStatus.FAIL),
        "checks_warn": sum(1 for r in results if r.status == CheckStatus.WARN),
        "checks_skip": sum(1 for r in results if r.status == CheckStatus.SKIP),
    }

    fails = [r for r in results if r.status == CheckStatus.FAIL]
    warns = [r for r in results if r.status == CheckStatus.WARN]

    details = format_text(results)
    # Appending the remediation block makes REPORT.md actionable.
    if fails or warns:
        details += "\n\nRemediation:\n"
        for r in fails + warns:
            if r.remediation:
                details += f"  - {r.name}: {r.remediation}\n"

    if fails:
        status = Status.FAIL
        summary = (
            f"{len(fails)} failure(s): " +
            ", ".join(r.name for r in fails[:3])
            + (" …" if len(fails) > 3 else "")
        )
    elif warns:
        status = Status.PASS
        summary = f"preflight OK ({len(warns)} warning(s))"
    else:
        status = Status.PASS
        summary = f"preflight OK ({len(results)} checks)"

    return PhaseResult(
        name="phase0_hw",
        status=status,
        summary=summary,
        details=details,
        metrics=metrics,
    )


# --- CLI entrypoint ---------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interface", "-i", default=None,
        help="Ethernet interface for hardware-dependent checks.",
    )
    args = parser.parse_args(argv)
    print_banner("Phase 0 (hardware) — extended preflight")
    ctx = RunContext.create_standalone(interface=args.interface)
    result = run_phase(ctx, "phase0_hw", phase0_hw)
    print_result(result)
    print(f"\nArtifacts: {ctx.run_dir}")
    ctx.close()
    return 0 if result.status in (Status.PASS, Status.SKIP) else 1


if __name__ == "__main__":
    sys.exit(main())
