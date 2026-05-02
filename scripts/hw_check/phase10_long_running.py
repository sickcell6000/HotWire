"""
Phase 10 — Long-running sustained-attack stability test.

Runs ForcedDischarge in a single Python process for ``--duration``
seconds (default 600 = 10 min), sampling RSS / open FD every minute.
The pass criterion is **no monotonic growth**: peak RSS at the end
must not exceed start RSS by more than 50%, and the FD count must
not climb by more than 20.

This catches:
  - PauseController accumulating override entries that never get GC'd
  - SDP responder thread leaking sockets on each restart
  - EXI codec / pcap RX buffer growing unbounded

Usage (Linux only — RSS/FD via /proc/self):
    sudo python3 scripts/hw_check/phase10_long_running.py \\
        --interface eth0 --duration 600 --voltage 380
"""
from __future__ import annotations

import argparse
import os
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


def _read_proc_status() -> dict:
    """Return RSS in KB and FD count for the current process."""
    out = {"rss_kb": -1, "fd_count": -1}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    out["rss_kb"] = int(parts[1])
                    break
    except (OSError, ValueError):
        pass
    try:
        out["fd_count"] = len(os.listdir("/proc/self/fd"))
    except OSError:
        pass
    return out


def phase10_long_running(
    ctx: RunContext,
    role: str,
    interface: str,
    duration_s: float,
    voltage: int,
    current: int,
    sample_interval_s: float,
) -> PhaseResult:
    from hotwire.attacks import ForcedDischarge
    from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController

    mode = {"pev": C_PEV_MODE, "evse": C_EVSE_MODE}[role]
    pause_controller = PauseController()
    if role == "evse":
        # Only the EVSE side has the ForcedDischarge override applied;
        # PEV side runs vanilla so it acts as the "victim" peer.
        ForcedDischarge(voltage=voltage, current=current).apply(pause_controller)

    class _Obs(MessageObserver):
        def __init__(self):
            self.cd_seen = 0   # rx CurrentDemand (Req for EVSE / Res for PEV)
            self.cd_sent = 0   # tx CurrentDemand (Res for EVSE / Req for PEV)
        def on_message(self, direction, stage, params):
            if not stage.startswith("CurrentDemand"):
                return
            if direction == "rx":
                self.cd_seen += 1
            elif direction == "tx":
                self.cd_sent += 1

    obs = _Obs()

    bpf = "ether proto 0x88E1 or ip6"
    metrics: dict = {
        "role": role, "interface": interface, "duration_s": duration_s,
        "voltage": voltage, "current": current,
    }
    artifacts = []

    samples: list[dict] = []
    start_status = _read_proc_status()
    samples.append({"t_s": 0.0, **start_status, "cd_seen": 0, "cd_sent": 0})

    with PacketCapture(
        ctx, phase="phase10", interface=interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            artifacts.append(cap.pcap_path)

        try:
            worker = HotWireWorker(
                callbackAddToTrace=lambda s: ctx.log.event(
                    kind="phase10.trace", message=s,
                ),
                callbackShowStatus=lambda *a, **kw: None,
                mode=mode,
                isSimulationMode=0,
                pause_controller=pause_controller,
                message_observer=obs,
            )
        except Exception as e:    # noqa: BLE001
            return PhaseResult(
                name="phase10_long_running", status=Status.ERROR,
                summary=f"failed to construct worker: {e}",
                metrics=metrics, artifacts=artifacts,
            )

        ctx.log.event(
            kind="phase10.start", duration_s=duration_s,
            sample_interval_s=sample_interval_s,
            initial_rss_kb=start_status["rss_kb"],
            initial_fd_count=start_status["fd_count"],
        )

        t0 = time.monotonic()
        deadline = t0 + duration_s
        next_sample = t0 + sample_interval_s
        while time.monotonic() < deadline:
            try:
                worker.mainfunction()
            except Exception:    # noqa: BLE001
                break
            if time.monotonic() >= next_sample:
                rec = _read_proc_status()
                rec["t_s"] = round(time.monotonic() - t0, 2)
                rec["cd_seen"] = obs.cd_seen
                rec["cd_sent"] = obs.cd_sent
                samples.append(rec)
                ctx.log.event(kind="phase10.sample", **rec)
                print(
                    f"  [{rec['t_s']:>7.1f}s] RSS={rec['rss_kb']:>7d} kB  "
                    f"FD={rec['fd_count']:>3d}  "
                    f"CD seen/sent={rec['cd_seen']}/{rec['cd_sent']}"
                )
                next_sample += sample_interval_s
            time.sleep(0.03)

        for attr in ("shutdown", "stop", "close"):
            fn = getattr(worker, attr, None)
            if callable(fn):
                try:
                    fn()
                except Exception:    # noqa: BLE001
                    pass
                break

    final_status = _read_proc_status()
    elapsed = time.monotonic() - t0
    samples.append({
        "t_s": round(elapsed, 2),
        **final_status,
        "cd_seen": obs.cd_seen,
        "cd_sent": obs.cd_sent,
    })

    rss_start = start_status["rss_kb"]
    rss_end = final_status["rss_kb"]
    fd_start = start_status["fd_count"]
    fd_end = final_status["fd_count"]
    rss_growth_pct = (
        100.0 * (rss_end - rss_start) / rss_start if rss_start > 0 else 0
    )
    fd_growth = fd_end - fd_start

    # Warmup-aware leak check: compare the FIRST post-warmup sample
    # (typically t=60s) to the LAST sample. The very first sample
    # (t=0) is taken before the worker fully boots, so RSS/FD legit
    # grow during the codec import + pcap open + SLAC startup. After
    # warmup, sustained activity should hold RSS / FD flat.
    post_warmup_samples = [s for s in samples if s["t_s"] >= sample_interval_s]
    if len(post_warmup_samples) >= 2:
        warm_rss_start = post_warmup_samples[0]["rss_kb"]
        warm_rss_end = post_warmup_samples[-1]["rss_kb"]
        warm_fd_start = post_warmup_samples[0]["fd_count"]
        warm_fd_end = post_warmup_samples[-1]["fd_count"]
        warm_rss_growth_pct = (
            100.0 * (warm_rss_end - warm_rss_start) / warm_rss_start
            if warm_rss_start > 0 else 0
        )
        warm_fd_growth = warm_fd_end - warm_fd_start
    else:
        warm_rss_growth_pct = rss_growth_pct
        warm_fd_growth = fd_growth

    metrics.update({
        "elapsed_s": round(elapsed, 3),
        "rss_kb_start": rss_start,
        "rss_kb_end": rss_end,
        "rss_growth_pct": round(rss_growth_pct, 1),
        "rss_growth_pct_post_warmup": round(warm_rss_growth_pct, 1),
        "fd_count_start": fd_start,
        "fd_count_end": fd_end,
        "fd_growth": fd_growth,
        "fd_growth_post_warmup": warm_fd_growth,
        "cd_seen_total": obs.cd_seen,
        "cd_sent_total": obs.cd_sent,
        "samples": samples,
    })

    if rss_start <= 0:
        return PhaseResult(
            name="phase10_long_running", status=Status.SKIP,
            summary="RSS not readable (Linux-only)",
            metrics=metrics, artifacts=artifacts,
        )

    failures = []
    # Use post-warmup growth for the leak verdict — see comment above.
    if warm_rss_growth_pct > 25.0:
        failures.append(
            f"RSS leaked post-warmup: {warm_rss_growth_pct:+.1f}% "
            f"(samples after warmup: {[s['rss_kb'] for s in post_warmup_samples]})"
        )
    if warm_fd_growth > 10:
        failures.append(
            f"FD leaked post-warmup: {warm_fd_growth:+d} "
            f"(samples: {[s['fd_count'] for s in post_warmup_samples]})"
        )
    # Activity floor: at least some CurrentDemand exchange happened.
    # Real value depends on whether the peer keeps reconnecting; we
    # only fail this when *zero* CD activity, which means the worker
    # never advanced to the CurrentDemand loop at all.
    if obs.cd_sent == 0 and obs.cd_seen == 0:
        failures.append(
            "no CurrentDemand activity at all — peer never connected"
        )

    if failures:
        return PhaseResult(
            name="phase10_long_running", status=Status.FAIL,
            summary="; ".join(failures),
            metrics=metrics, artifacts=artifacts,
        )
    return PhaseResult(
        name="phase10_long_running", status=Status.PASS,
        summary=(
            f"{elapsed:.0f}s sustained: RSS {rss_start}→{rss_end} kB "
            f"(post-warmup {warm_rss_growth_pct:+.1f}%), "
            f"FD {fd_start}→{fd_end} "
            f"(post-warmup {warm_fd_growth:+d}), CD sent={obs.cd_sent}"
        ),
        metrics=metrics, artifacts=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    _seed_offscreen_qt()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--role", choices=("pev", "evse"), default="evse")
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--voltage", type=int, default=380)
    parser.add_argument("--current", type=int, default=10)
    parser.add_argument("--sample-interval", type=float, default=60.0)
    args = parser.parse_args(argv)

    print_banner(
        f"Phase 10 — long-running sustained ({args.duration:.0f}s)"
    )
    ctx = RunContext.create_standalone(interface=args.interface)
    try:
        result = phase10_long_running(
            ctx, args.role, args.interface, args.duration, args.voltage,
            args.current, args.sample_interval,
        )
    finally:
        ctx.close()
    print_result(result)
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
