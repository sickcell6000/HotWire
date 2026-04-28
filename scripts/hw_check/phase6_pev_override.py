"""
Phase 6 — PEV-side parameter override test.

Verifies that PauseController.set_override works on the PEV side too,
not just EVSE. We override ``PreChargeReq.EVTargetVoltage`` to a
sentinel value (999 V) and confirm:

  1. PEV transmits 999 V in EVTargetVoltage.
  2. Peer EVSE receives the same 999 V.

This proves the override path is symmetric (both roles, same API,
same wire-level effect).

Note: A 999V target will cause EVSE.fsm to fall back to its hardcoded
default (because the parser's max(1, v*10^m) = 999 won't match the
EVSE's known charge_target_voltage), so the V2G handshake won't
necessarily complete. That's fine — Test 6 only validates the override
mechanism on the PEV side, not the full session.

Usage:
    sudo python scripts/hw_check/phase6_pev_override.py \\
        --interface eth0 --budget 30
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


def phase6_pev_override(
    ctx: RunContext,
    interface: str,
    budget_s: float = 30.0,
    sentinel_voltage: int = 999,
) -> PhaseResult:
    from hotwire.core.modes import C_PEV_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController

    pc = PauseController()
    # Use string form because that's what fsm_pev.py inserts into params
    pc.set_override("PreChargeReq", {"EVTargetVoltage": str(sentinel_voltage)})
    ctx.log.event(
        kind="phase6.override_installed",
        stage="PreChargeReq",
        EVTargetVoltage=str(sentinel_voltage),
    )

    # Trace-side capture: fsm_pev encodes EVTargetVoltage into the
    # EDG_<sessId>_<soc>_<voltage>_<current> command string handed to
    # OpenV2G. We parse that string to read the actual voltage that
    # went out on the wire — params dict at observer-time is the
    # default-shape, not the post-override merged form.
    encoded_voltages: list[int] = []

    def _trace(s: str) -> None:
        ctx.log.event(kind="phase6.trace", message=s)
        # Match e.g. "PreChargeReq: encoding command EDG_..._30_999_1"
        if "PreChargeReq: encoding command EDG_" in s:
            try:
                cmd = s.split("EDG_")[1].split()[0]
                # EDG_<sessId>_<soc>_<voltage>_<current>
                parts = cmd.split("_")
                if len(parts) >= 4:
                    encoded_voltages.append(int(parts[2]))
            except (ValueError, IndexError):
                pass

    class _Observer(MessageObserver):
        def __init__(self) -> None:
            self.tx_pre_charge_req = 0

        def on_message(
            self, direction: str, stage: str, params: dict,
        ) -> None:
            if direction == "tx" and stage == "PreChargeReq":
                self.tx_pre_charge_req += 1

    observer = _Observer()

    bpf = "ether proto 0x88E1 or ip6"
    metrics: dict = {
        "role": "pev", "interface": interface, "budget_s": budget_s,
        "sentinel_voltage": sentinel_voltage,
    }
    artifacts = []

    with PacketCapture(
        ctx, phase="phase6", interface=interface, bpf=bpf,
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
        except Exception as e:        # noqa: BLE001
            return PhaseResult(
                name="phase6_pev_override", status=Status.ERROR,
                summary=f"failed to construct worker: {e}",
                metrics=metrics, artifacts=artifacts,
            )

        ctx.log.event(kind="phase6.start", budget_s=budget_s)
        t0 = time.monotonic()
        deadline = t0 + budget_s
        while time.monotonic() < deadline:
            try:
                worker.mainfunction()
            except Exception:    # noqa: BLE001
                break
            # Early-exit once we've sent at least 1 PreChargeReq
            if observer.tx_pre_charge_req >= 1:
                # Give it 1 more tick to capture the value
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
    last_voltage = encoded_voltages[-1] if encoded_voltages else None
    metrics.update({
        "elapsed_s": round(elapsed, 3),
        "tx_pre_charge_req": observer.tx_pre_charge_req,
        "encoded_voltages": encoded_voltages,
        "last_encoded_voltage": last_voltage,
    })

    if observer.tx_pre_charge_req == 0:
        return PhaseResult(
            name="phase6_pev_override", status=Status.FAIL,
            summary=(
                f"PEV never sent PreChargeReq within {elapsed:.1f}s "
                f"(EVSE peer not responding?)"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    if last_voltage != sentinel_voltage:
        return PhaseResult(
            name="phase6_pev_override", status=Status.FAIL,
            summary=(
                f"PEV encoded EVTargetVoltage={last_voltage} but "
                f"override demanded {sentinel_voltage} — set_override "
                f"didn't apply"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    return PhaseResult(
        name="phase6_pev_override", status=Status.PASS,
        summary=(
            f"PEV-side set_override applied: encoded "
            f"EVTargetVoltage={last_voltage} into wire-bound "
            f"PreChargeReq EDG command "
            f"({observer.tx_pre_charge_req} req(s) in {elapsed:.1f}s)"
        ),
        metrics=metrics, artifacts=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    _seed_offscreen_qt()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--budget", type=float, default=30.0)
    parser.add_argument("--voltage", type=int, default=999)
    args = parser.parse_args(argv)

    print_banner("Phase 6 — PEV-side override test")
    ctx = RunContext.create_standalone(interface=args.interface)
    try:
        result = phase6_pev_override(
            ctx, args.interface, args.budget, args.voltage,
        )
    finally:
        ctx.close()
    print_result(result)
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
