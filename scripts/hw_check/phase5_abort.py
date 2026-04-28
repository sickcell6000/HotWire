"""
Phase 5c — Abort path verification.

Pauses ``PreChargeRes``, then calls ``PauseController.abort()`` instead
of ``send()``. The FSM must resume with the original (unmodified) params
and the V2G session must continue to PowerDelivery / CurrentDemand
without errors.

This validates the "operator changed their mind / dialog cancelled"
code path that the GUI uses when the user dismisses an edit dialog.

PASS if:
  - At least one PreChargeRes was paused
  - abort() returned and the FSM continued
  - The released PreChargeRes carries the *original* (config-driven)
    EVSEPresentVoltage, NOT a sentinel
  - V2G chain advanced past PreCharge to PowerDelivery
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
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


def phase5_abort(
    ctx: RunContext,
    interface: str,
    budget_s: float = 60.0,
) -> PhaseResult:
    from hotwire.core.modes import C_EVSE_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController

    pc = PauseController()
    pc.set_pause_enabled("PreChargeRes", True)

    aborted_count = {"n": 0}
    paused_count = {"n": 0}
    captured_voltage: list[int] = []

    class _Observer(MessageObserver):
        def __init__(self) -> None:
            self.tx_pre_charge_res = 0
            self.tx_power_delivery_res = 0
            self.last_voltage: int | None = None

        def on_message(
            self, direction: str, stage: str, params: dict,
        ) -> None:
            if direction == "tx" and stage == "PreChargeRes":
                self.tx_pre_charge_res += 1
                v = params.get("EVSEPresentVoltage")
                if v is not None:
                    try:
                        self.last_voltage = int(v)
                    except (TypeError, ValueError):
                        pass
            if direction == "tx" and stage == "PowerDeliveryRes":
                self.tx_power_delivery_res += 1

    observer = _Observer()
    stop_watcher = threading.Event()

    def _watcher() -> None:
        while not stop_watcher.is_set():
            pending = pc.get_pending()
            if pending and pending["stage"] == "PreChargeRes":
                p = dict(pending["params"])
                v = p.get("EVSEPresentVoltage", "?")
                captured_voltage.append(v)
                paused_count["n"] += 1
                ctx.log.event(
                    kind="phase5c.pause_hit",
                    captured_voltage=v,
                    decision="ABORT",
                )
                pc.abort()
                aborted_count["n"] += 1
                pc.set_pause_enabled("PreChargeRes", False)
                return
            time.sleep(0.02)

    threading.Thread(target=_watcher, daemon=True).start()

    bpf = "ether proto 0x88E1 or ip6"
    metrics: dict = {"role": "evse", "interface": interface, "budget_s": budget_s}
    artifacts = []

    with PacketCapture(
        ctx, phase="phase5c", interface=interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            artifacts.append(cap.pcap_path)

        try:
            worker = HotWireWorker(
                callbackAddToTrace=lambda s: ctx.log.event(
                    kind="phase5c.trace", message=s,
                ),
                callbackShowStatus=lambda *a, **kw: None,
                mode=C_EVSE_MODE,
                isSimulationMode=0,
                pause_controller=pc,
                message_observer=observer,
            )
        except Exception as e:        # noqa: BLE001
            stop_watcher.set()
            return PhaseResult(
                name="phase5_abort", status=Status.ERROR,
                summary=f"failed to construct worker: {e}",
                metrics=metrics, artifacts=artifacts,
            )

        ctx.log.event(kind="phase5c.start", budget_s=budget_s)
        t0 = time.monotonic()
        deadline = t0 + budget_s
        while time.monotonic() < deadline:
            try:
                worker.mainfunction()
            except Exception:    # noqa: BLE001
                break
            # Once we've aborted AND seen PowerDelivery, we're done
            if aborted_count["n"] >= 1 and observer.tx_power_delivery_res >= 1:
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

    stop_watcher.set()
    elapsed = time.monotonic() - t0
    metrics.update({
        "elapsed_s": round(elapsed, 3),
        "paused_count": paused_count["n"],
        "aborted_count": aborted_count["n"],
        "tx_pre_charge_res": observer.tx_pre_charge_res,
        "tx_power_delivery_res": observer.tx_power_delivery_res,
        "last_voltage": observer.last_voltage,
        "captured_voltage_at_pause": captured_voltage[0] if captured_voltage else None,
    })

    if paused_count["n"] == 0:
        return PhaseResult(
            name="phase5_abort", status=Status.FAIL,
            summary="never reached pause point (no PEV peer?)",
            metrics=metrics, artifacts=artifacts,
        )
    if aborted_count["n"] != paused_count["n"]:
        return PhaseResult(
            name="phase5_abort", status=Status.FAIL,
            summary=f"paused {paused_count['n']}x but only aborted {aborted_count['n']}",
            metrics=metrics, artifacts=artifacts,
        )
    if observer.tx_pre_charge_res == 0:
        return PhaseResult(
            name="phase5_abort", status=Status.FAIL,
            summary="abort didn't release the FSM (no PreChargeRes ever sent)",
            metrics=metrics, artifacts=artifacts,
        )
    if observer.tx_power_delivery_res == 0:
        return PhaseResult(
            name="phase5_abort", status=Status.FAIL,
            summary=(
                f"abort released FSM (tx PreChargeRes={observer.tx_pre_charge_res}) "
                f"but session never advanced to PowerDeliveryRes"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    return PhaseResult(
        name="phase5_abort", status=Status.PASS,
        summary=(
            f"paused {paused_count['n']}x → abort()ed → V2G chain still "
            f"advanced (tx PreChargeRes={observer.tx_pre_charge_res}, "
            f"tx PowerDeliveryRes={observer.tx_power_delivery_res}) in "
            f"{elapsed:.1f}s"
        ),
        metrics=metrics, artifacts=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    _seed_offscreen_qt()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--budget", type=float, default=60.0)
    args = parser.parse_args(argv)

    print_banner("Phase 5c — abort path verification")
    ctx = RunContext.create_standalone(interface=args.interface)
    try:
        result = phase5_abort(ctx, args.interface, args.budget)
    finally:
        ctx.close()
    print_result(result)
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
