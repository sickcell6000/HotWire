"""
Phase 9 — Fuzz-like single-shot session.

One worker session per invocation. Caller passes random / out-of-band
attack values; the script applies them and reports what reached the
wire. Used by an external bash harness that loops, picking different
random values each run, so each iteration is its own clean process —
sidesteps the worker-reuse bug that phase7_stress exposed.

Each run picks (or accepts) random:
  - EVCCID (12 hex chars, possibly out of normal MAC OUI space)
  - PreCharge EVTargetVoltage (1..2000 V — sometimes way out of band)
  - CurrentDemand EVTargetCurrent (0..500 A)

Records what we *tried* to send vs what the encoder actually emitted,
plus whether the peer rejected anything (FAIL handshake).

Usage from the bash harness:
    sudo python3 scripts/hw_check/phase9_fuzz.py \\
        --interface eth0 --role pev --budget 25 \\
        --evccid 0123456789ab --voltage 1500 --current 200

If --voltage / --current / --evccid are omitted, random values are
chosen and reported in the result.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))
sys.path.insert(0, str(_THIS.parent.parent.parent))

from _runner import (    # type: ignore[import-not-found]  # noqa: E402
    PacketCapture, PhaseResult, RunContext, Status, print_banner, print_result,
)


_EVCCID_PATTERN = re.compile(r"^[0-9a-fA-F]{12}$")


def _seed_offscreen_qt() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def phase9_fuzz_pev(
    ctx: RunContext,
    interface: str,
    budget_s: float,
    evccid: str,
    voltage: int,
    current: int,
) -> PhaseResult:
    from hotwire.core.modes import C_PEV_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController

    pc = PauseController()
    pc.set_override("SessionSetupReq", {"EVCCID": evccid})
    pc.set_override("PreChargeReq", {
        "EVTargetVoltage": str(voltage),
        "EVTargetCurrent": str(current),
    })
    pc.set_override("CurrentDemandReq", {
        "EVTargetVoltage": str(voltage),
        "EVTargetCurrent": str(current),
    })
    ctx.log.event(
        kind="phase9.config",
        evccid=evccid, voltage=voltage, current=current,
    )

    encoded_evccids: list[str] = []
    encoded_voltages: list[int] = []
    encoded_currents: list[int] = []

    def _trace(s: str) -> None:
        ctx.log.event(kind="phase9.trace", message=s)
        if "SessionSetupReq: encoding command EDA_" in s:
            try:
                encoded_evccids.append(s.split("EDA_")[1].split()[0])
            except (ValueError, IndexError):
                pass
        if "PreChargeReq: encoding command EDG_" in s:
            try:
                parts = s.split("EDG_")[1].split()[0].split("_")
                if len(parts) >= 4:
                    encoded_voltages.append(int(parts[2]))
                    encoded_currents.append(int(parts[3]))
            except (ValueError, IndexError):
                pass
        if "CurrentDemandReq: encoding command EDI_" in s:
            try:
                parts = s.split("EDI_")[1].split()[0].split("_")
                # EDI_<sessId>_<soc>_<currentA>_<voltageV>
                if len(parts) >= 5:
                    encoded_currents.append(int(parts[3]))
                    encoded_voltages.append(int(parts[4]))
            except (ValueError, IndexError):
                pass

    class _Observer(MessageObserver):
        def __init__(self) -> None:
            self.tx_counts: dict[str, int] = {}
            self.rx_counts: dict[str, int] = {}

        def on_message(self, direction, stage, params) -> None:
            target = self.tx_counts if direction == "tx" else self.rx_counts
            target[stage] = target.get(stage, 0) + 1

    observer = _Observer()

    bpf = "ether proto 0x88E1 or ip6"
    metrics: dict = {
        "role": "pev", "interface": interface, "budget_s": budget_s,
        "fuzz_evccid": evccid, "fuzz_voltage": voltage, "fuzz_current": current,
    }
    artifacts = []

    with PacketCapture(
        ctx, phase="phase9", interface=interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            artifacts.append(cap.pcap_path)

        try:
            worker = HotWireWorker(
                callbackAddToTrace=_trace,
                callbackShowStatus=lambda *a, **kw: None,
                mode=C_PEV_MODE,
                isSimulationMode=0,
                pause_controller=pc,
                message_observer=observer,
            )
        except Exception as e:    # noqa: BLE001
            return PhaseResult(
                name="phase9_fuzz", status=Status.ERROR,
                summary=f"failed to construct worker: {e}",
                metrics=metrics, artifacts=artifacts,
            )

        ctx.log.event(kind="phase9.start", budget_s=budget_s)
        t0 = time.monotonic()
        deadline = t0 + budget_s
        while time.monotonic() < deadline:
            try:
                worker.mainfunction()
            except Exception:    # noqa: BLE001
                break
            # Short circuit once we've sent at least PreChargeReq
            if observer.tx_counts.get("PreChargeReq", 0) >= 1:
                time.sleep(0.05)
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

    elapsed = time.monotonic() - t0
    metrics.update({
        "elapsed_s": round(elapsed, 3),
        "tx_counts": observer.tx_counts,
        "rx_counts": observer.rx_counts,
        "encoded_evccids": encoded_evccids,
        "encoded_voltages": encoded_voltages,
        "encoded_currents": encoded_currents,
        "evccid_match": evccid in encoded_evccids,
        "voltage_match": voltage in encoded_voltages,
        "current_match": current in encoded_currents,
    })

    # Fuzz PASS: did the attack values reach the wire? (Doesn't matter
    # if peer rejects them — we're proving HotWire emitted them.)
    if not encoded_evccids:
        return PhaseResult(
            name="phase9_fuzz", status=Status.FAIL,
            summary="no SessionSetupReq encoded — session never started",
            metrics=metrics, artifacts=artifacts,
        )
    if evccid not in encoded_evccids:
        return PhaseResult(
            name="phase9_fuzz", status=Status.FAIL,
            summary=f"EVCCID {evccid} not in encoded {encoded_evccids}",
            metrics=metrics, artifacts=artifacts,
        )
    if not encoded_voltages:
        return PhaseResult(
            name="phase9_fuzz", status=Status.FAIL,
            summary=(
                f"no PreChargeReq encoded — peer EVSE didn't accept "
                f"upstream messages (rx={observer.rx_counts})"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    if voltage not in encoded_voltages:
        return PhaseResult(
            name="phase9_fuzz", status=Status.FAIL,
            summary=(
                f"voltage {voltage} not encoded "
                f"(saw {encoded_voltages}) — override leaked"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    return PhaseResult(
        name="phase9_fuzz", status=Status.PASS,
        summary=(
            f"fuzz values reached wire: EVCCID={evccid}, "
            f"V={voltage}, I={current} — encoded {len(encoded_evccids)}/"
            f"{len(encoded_voltages)} req(s) in {elapsed:.1f}s"
        ),
        metrics=metrics, artifacts=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    _seed_offscreen_qt()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--role", choices=("pev",), default="pev")
    parser.add_argument("--budget", type=float, default=25.0)
    parser.add_argument("--evccid", default="")
    parser.add_argument("--voltage", type=int, default=-1)
    parser.add_argument("--current", type=int, default=-1)
    parser.add_argument(
        "--seed", type=int, default=-1,
        help="Random seed for reproducibility (default: time-based)",
    )
    args = parser.parse_args(argv)

    rng = random.Random(args.seed if args.seed >= 0 else time.time_ns())

    evccid = args.evccid or "".join(rng.choice("0123456789abcdef") for _ in range(12))
    if not _EVCCID_PATTERN.fullmatch(evccid):
        print(f"ERROR: invalid EVCCID '{evccid}'", file=sys.stderr)
        return 2
    voltage = args.voltage if args.voltage >= 0 else rng.randint(1, 2000)
    current = args.current if args.current >= 0 else rng.randint(0, 500)

    fuzz_input = {
        "evccid": evccid, "voltage": voltage, "current": current,
    }
    print(f"[fuzz-input] {json.dumps(fuzz_input)}")

    print_banner("Phase 9 — fuzz-like single-shot")
    ctx = RunContext.create_standalone(interface=args.interface)
    try:
        result = phase9_fuzz_pev(
            ctx, args.interface, args.budget, evccid, voltage, current,
        )
    finally:
        ctx.close()
    print_result(result)
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
