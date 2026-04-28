"""
Phase 8 — Concurrent override + pause-and-edit combination.

Stress-tests the PauseController by combining two independent attack
techniques in a single PEV session:

  - ``set_override`` on ``PreChargeReq.EVTargetVoltage`` → 999 V
    (stays in effect for every PreChargeReq the FSM emits)

  - ``set_pause_enabled`` on ``SessionSetupReq`` → watcher edits
    ``EVCCID`` to a sentinel before releasing
    (only fires once at the start of the session)

Confirms the two paths don't interfere — pause-edit on one stage works
correctly while a static override is active on a different stage.

PASS criteria:
  1. SessionSetupReq paused exactly once (set_pause_enabled fired)
  2. The released SessionSetupReq carried the spoofed EVCCID
  3. Every subsequent PreChargeReq carried EVTargetVoltage=999
     (override never lost)

Usage:
    sudo python3 scripts/hw_check/phase8_concurrent.py \\
        --interface eth0 --budget 35
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


def phase8_concurrent(
    ctx: RunContext,
    interface: str,
    budget_s: float = 35.0,
    sentinel_voltage: int = 999,
    sentinel_evccid: str = "feedfacecafe",
) -> PhaseResult:
    from hotwire.core.modes import C_PEV_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController

    pc = PauseController()
    # Static override across the whole session
    pc.set_override(
        "PreChargeReq", {"EVTargetVoltage": str(sentinel_voltage)},
    )
    # Pause-and-edit on a different stage
    pc.set_pause_enabled("SessionSetupReq", True)
    ctx.log.event(
        kind="phase8.config",
        override_stage="PreChargeReq",
        override_value=sentinel_voltage,
        pause_stage="SessionSetupReq",
        pause_evccid=sentinel_evccid,
    )

    pause_hits = {"n": 0}
    pause_released = {"n": 0}
    encoded_voltages: list[int] = []
    encoded_evccids: list[str] = []

    def _trace(s: str) -> None:
        ctx.log.event(kind="phase8.trace", message=s)
        # PEV PreChargeReq: encoding command EDG_<sessId>_<soc>_<voltage>_<current>
        if "PreChargeReq: encoding command EDG_" in s:
            try:
                cmd = s.split("EDG_")[1].split()[0]
                parts = cmd.split("_")
                if len(parts) >= 4:
                    encoded_voltages.append(int(parts[2]))
            except (ValueError, IndexError):
                pass
        # SessionSetupReq: encoding command EDA_<evccid>
        if "SessionSetupReq: encoding command EDA_" in s:
            try:
                cmd = s.split("EDA_")[1].split()[0]
                encoded_evccids.append(cmd)
            except (ValueError, IndexError):
                pass

    class _Observer(MessageObserver):
        def __init__(self) -> None:
            self.tx_session_setup_req = 0
            self.tx_pre_charge_req = 0

        def on_message(self, direction, stage, params) -> None:
            if direction == "tx":
                if stage == "SessionSetupReq":
                    self.tx_session_setup_req += 1
                elif stage == "PreChargeReq":
                    self.tx_pre_charge_req += 1

    observer = _Observer()
    stop_watcher = threading.Event()

    def _watcher() -> None:
        while not stop_watcher.is_set():
            pending = pc.get_pending()
            if pending and pending["stage"] == "SessionSetupReq":
                p = dict(pending["params"])
                p["EVCCID"] = sentinel_evccid
                pause_hits["n"] += 1
                ctx.log.event(
                    kind="phase8.pause_hit",
                    stage="SessionSetupReq", new_evccid=sentinel_evccid,
                )
                pc.send(p)
                pause_released["n"] += 1
                pc.set_pause_enabled("SessionSetupReq", False)
                return
            time.sleep(0.02)

    threading.Thread(target=_watcher, daemon=True).start()

    bpf = "ether proto 0x88E1 or ip6"
    metrics: dict = {
        "role": "pev", "interface": interface, "budget_s": budget_s,
        "sentinel_voltage": sentinel_voltage,
        "sentinel_evccid": sentinel_evccid,
    }
    artifacts = []

    with PacketCapture(
        ctx, phase="phase8", interface=interface, bpf=bpf,
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
            stop_watcher.set()
            return PhaseResult(
                name="phase8_concurrent", status=Status.ERROR,
                summary=f"failed to construct worker: {e}",
                metrics=metrics, artifacts=artifacts,
            )

        ctx.log.event(kind="phase8.start", budget_s=budget_s)
        t0 = time.monotonic()
        deadline = t0 + budget_s
        while time.monotonic() < deadline:
            try:
                worker.mainfunction()
            except Exception:    # noqa: BLE001
                break
            # Done once we've seen at least 1 of each — that's the
            # critical moment where override + pause both proven
            if (observer.tx_pre_charge_req >= 1 and pause_hits["n"] >= 1):
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
        "pause_hits": pause_hits["n"],
        "pause_released": pause_released["n"],
        "tx_session_setup_req": observer.tx_session_setup_req,
        "tx_pre_charge_req": observer.tx_pre_charge_req,
        "encoded_voltages": encoded_voltages,
        "encoded_evccids": encoded_evccids,
        "all_voltages_match_sentinel": all(
            v == sentinel_voltage for v in encoded_voltages
        ),
    })

    if pause_hits["n"] == 0:
        return PhaseResult(
            name="phase8_concurrent", status=Status.FAIL,
            summary="SessionSetupReq pause never fired",
            metrics=metrics, artifacts=artifacts,
        )
    if pause_released["n"] != pause_hits["n"]:
        return PhaseResult(
            name="phase8_concurrent", status=Status.FAIL,
            summary=(
                f"paused {pause_hits['n']}x but only released "
                f"{pause_released['n']}"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    if not encoded_evccids or sentinel_evccid not in encoded_evccids:
        return PhaseResult(
            name="phase8_concurrent", status=Status.FAIL,
            summary=(
                f"pause edit didn't reach the wire — encoded EVCCIDs: "
                f"{encoded_evccids} (expected {sentinel_evccid})"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    if not encoded_voltages:
        return PhaseResult(
            name="phase8_concurrent", status=Status.FAIL,
            summary="no PreChargeReq encoded — session never advanced",
            metrics=metrics, artifacts=artifacts,
        )
    if not metrics["all_voltages_match_sentinel"]:
        wrong = [v for v in encoded_voltages if v != sentinel_voltage]
        return PhaseResult(
            name="phase8_concurrent", status=Status.FAIL,
            summary=(
                f"override leaked: PreChargeReq.EVTargetVoltage = "
                f"{encoded_voltages} (some not {sentinel_voltage}: {wrong})"
            ),
            metrics=metrics, artifacts=artifacts,
        )
    return PhaseResult(
        name="phase8_concurrent", status=Status.PASS,
        summary=(
            f"both attacks active: SessionSetupReq EVCCID→{sentinel_evccid} "
            f"(pause-and-edit, {pause_hits['n']}x), "
            f"PreChargeReq EVTargetVoltage→{sentinel_voltage} (override, "
            f"{len(encoded_voltages)} req(s)) in {elapsed:.1f}s"
        ),
        metrics=metrics, artifacts=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    _seed_offscreen_qt()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--budget", type=float, default=35.0)
    parser.add_argument("--voltage", type=int, default=999)
    parser.add_argument("--evccid", default="feedfacecafe")
    args = parser.parse_args(argv)

    print_banner("Phase 8 — concurrent override + pause-and-edit")
    ctx = RunContext.create_standalone(interface=args.interface)
    try:
        result = phase8_concurrent(
            ctx, args.interface, args.budget, args.voltage, args.evccid,
        )
    finally:
        ctx.close()
    print_result(result)
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
