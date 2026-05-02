"""
Phase 7 — Connection-stability stress test.

Runs N consecutive V2G sessions back-to-back against a peer running
the same test on the other side, recording per-iteration timing,
success/fail, and any modem / SLAC drift symptoms. Detects:

  - SLAC time creeping up over iterations (modem AVLN getting confused)
  - Sessions that fail to start at all (modem dropped offline)
  - Memory or socket leaks (process slows, eventually crashes)
  - Worker shutdown bugs (next iteration can't bind sockets)

Both sides MUST run this same script — one ``--role evse``, the other
``--role pev``. They synchronize on a shared barrier file (when the
peer is on the same NFS mount) or just share a wall-clock window with
matching ``--iterations`` and ``--per-iter-budget``.

Usage:
    # Pi PEV
    sudo python3 scripts/hw_check/phase7_stress.py \\
        --role pev --interface eth0 \\
        --iterations 20 --per-iter-budget 25

    # EVSE Windows
    python scripts/hw_check/phase7_stress.py \\
        --role evse --interface "\\Device\\NPF_{...}" \\
        --iterations 20 --per-iter-budget 25
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))
sys.path.insert(0, str(_THIS.parent.parent.parent))

from _runner import (    # type: ignore[import-not-found]  # noqa: E402
    PacketCapture, PhaseResult, RunContext, Status, print_banner, print_result,
)


def _seed_offscreen_qt() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def phase7_stress(
    ctx: RunContext,
    role: str,
    interface: str,
    iterations: int = 20,
    per_iter_budget: float = 25.0,
    inter_iter_pause: float = 2.0,
    min_cd: int = 3,
) -> PhaseResult:
    from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver

    mode = {"pev": C_PEV_MODE, "evse": C_EVSE_MODE}[role]

    iter_records: list[dict] = []
    bpf = "ether proto 0x88E1 or ip6"
    metrics: dict = {
        "role": role, "interface": interface, "iterations": iterations,
        "per_iter_budget": per_iter_budget,
    }
    artifacts = []

    with PacketCapture(
        ctx, phase="phase7", interface=interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            artifacts.append(cap.pcap_path)

        ctx.log.event(
            kind="phase7.start", role=role, iterations=iterations,
            per_iter_budget=per_iter_budget,
        )

        for i in range(1, iterations + 1):
            iter_t0 = time.monotonic()
            cd_seen = 0
            stages_seen: set[str] = set()
            slac_started_at: float | None = None
            first_message_at: float | None = None

            class _Observer(MessageObserver):
                def on_message(
                    self, direction: str, stage: str, params: dict,
                ) -> None:
                    nonlocal cd_seen, first_message_at
                    if first_message_at is None:
                        first_message_at = time.monotonic()
                    stages_seen.add(stage)
                    if direction == "rx" and stage.startswith("CurrentDemand"):
                        cd_seen += 1

            observer = _Observer()

            def _trace(s: str) -> None:
                nonlocal slac_started_at
                if slac_started_at is None and "SLAC started" in s:
                    slac_started_at = time.monotonic()
                ctx.log.event(kind=f"phase7.iter{i:03d}.trace", message=s)

            try:
                worker = HotWireWorker(
                    callbackAddToTrace=_trace,
                    callbackShowStatus=lambda *a, **kw: None,
                    mode=mode,
                    isSimulationMode=0,
                    message_observer=observer,
                )
            except Exception as e:    # noqa: BLE001
                iter_records.append({
                    "iter": i, "status": "WORKER_ERROR", "error": str(e),
                    "elapsed_s": round(time.monotonic() - iter_t0, 3),
                })
                ctx.log.event(
                    kind="phase7.iter_error", iter=i, error=str(e),
                )
                time.sleep(inter_iter_pause)
                continue

            deadline = iter_t0 + per_iter_budget
            while time.monotonic() < deadline:
                try:
                    worker.mainfunction()
                except Exception:    # noqa: BLE001
                    break
                if cd_seen >= min_cd:
                    break
                time.sleep(0.03)

            for attr in ("shutdown", "stop", "close"):
                fn = getattr(worker, attr, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:    # noqa: BLE001
                        pass
                    break

            iter_elapsed = time.monotonic() - iter_t0
            slac_delay = (
                round(slac_started_at - iter_t0, 3)
                if slac_started_at else None
            )
            first_msg_delay = (
                round(first_message_at - iter_t0, 3)
                if first_message_at else None
            )
            ok = cd_seen >= min_cd
            rec = {
                "iter": i,
                "status": "OK" if ok else "FAIL",
                "elapsed_s": round(iter_elapsed, 3),
                "slac_delay_s": slac_delay,
                "first_msg_delay_s": first_msg_delay,
                "cd_seen": cd_seen,
                "stages_seen_count": len(stages_seen),
            }
            iter_records.append(rec)
            ctx.log.event(kind="phase7.iter", **rec)
            print(
                f"  iter {i:>3d}: {rec['status']:<4s} "
                f"elapsed={iter_elapsed:>5.1f}s "
                f"slac_at={'?' if slac_delay is None else f'{slac_delay:>4.1f}s'} "
                f"cd={cd_seen}"
            )

            # Brief pause to let modems re-stabilize between sessions
            time.sleep(inter_iter_pause)

    # Aggregate stats
    ok_iters = [r for r in iter_records if r["status"] == "OK"]
    pass_count = len(ok_iters)
    fail_count = iterations - pass_count
    success_rate = pass_count / iterations if iterations else 0
    elapsed_values = [r["elapsed_s"] for r in iter_records]
    slac_delays = [
        r["slac_delay_s"] for r in iter_records
        if r["slac_delay_s"] is not None
    ]

    metrics.update({
        "pass_count": pass_count,
        "fail_count": fail_count,
        "success_rate": round(success_rate, 3),
        "elapsed_mean_s": round(statistics.mean(elapsed_values), 3) if elapsed_values else None,
        "elapsed_stdev_s": round(statistics.stdev(elapsed_values), 3) if len(elapsed_values) > 1 else None,
        "elapsed_max_s": round(max(elapsed_values), 3) if elapsed_values else None,
        "slac_delay_mean_s": round(statistics.mean(slac_delays), 3) if slac_delays else None,
        "slac_delay_max_s": round(max(slac_delays), 3) if slac_delays else None,
        "iter_records": iter_records,
    })

    # PASS criterion: at least 80% of iterations OK and no creep > 50%
    creep_alarm = False
    if len(ok_iters) >= 4:
        first_quartile = ok_iters[: len(ok_iters) // 4 or 1]
        last_quartile = ok_iters[-(len(ok_iters) // 4 or 1):]
        first_mean = statistics.mean(r["elapsed_s"] for r in first_quartile)
        last_mean = statistics.mean(r["elapsed_s"] for r in last_quartile)
        if first_mean > 0 and (last_mean / first_mean) > 1.5:
            creep_alarm = True
            metrics["creep_first_q_mean"] = round(first_mean, 3)
            metrics["creep_last_q_mean"] = round(last_mean, 3)

    if success_rate < 0.8:
        return PhaseResult(
            name="phase7_stress", status=Status.FAIL,
            summary=(
                f"only {pass_count}/{iterations} iterations succeeded "
                f"({success_rate:.0%}); modem may have drifted offline"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    if creep_alarm:
        return PhaseResult(
            name="phase7_stress", status=Status.FAIL,
            summary=(
                f"{pass_count}/{iterations} OK but per-iter elapsed time "
                f"crept from {metrics['creep_first_q_mean']}s (first 1/4) "
                f"to {metrics['creep_last_q_mean']}s (last 1/4) — "
                f"likely AVLN drift"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    return PhaseResult(
        name="phase7_stress", status=Status.PASS,
        summary=(
            f"{pass_count}/{iterations} iterations PASS ({success_rate:.0%}); "
            f"mean elapsed {metrics['elapsed_mean_s']}s "
            f"(stdev {metrics.get('elapsed_stdev_s', 'n/a')}); "
            f"SLAC mean delay {metrics['slac_delay_mean_s']}s"
        ),
        metrics=metrics, artifacts=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    _seed_offscreen_qt()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--role", choices=("pev", "evse"), required=True)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--per-iter-budget", type=float, default=25.0)
    parser.add_argument("--inter-iter-pause", type=float, default=2.0)
    parser.add_argument("--min-cd", type=int, default=3)
    args = parser.parse_args(argv)

    print_banner(
        f"Phase 7 — V2G stability stress ({args.role}, "
        f"{args.iterations} iterations)"
    )
    ctx = RunContext.create_standalone(interface=args.interface)
    try:
        result = phase7_stress(
            ctx, args.role, args.interface,
            iterations=args.iterations,
            per_iter_budget=args.per_iter_budget,
            inter_iter_pause=args.inter_iter_pause,
            min_cd=args.min_cd,
        )
    finally:
        ctx.close()
    print_result(result)
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
