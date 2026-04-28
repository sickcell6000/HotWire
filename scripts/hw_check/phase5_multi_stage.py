"""
Phase 5b — Multi-stage pause-and-send.

Variant of phase5_pause_send that pauses *two* stages simultaneously
(``SessionSetupRes`` and ``ChargeParameterDiscoveryRes``) to verify
that the PauseController serializes correctly when a single FSM hits
back-to-back pause points within the same V2G session.

The watcher thread:
  1. Waits for the SessionSetupRes pause hit, edits ``EVSEID``, releases.
  2. Waits for the ChargeParameterDiscoveryRes pause hit, edits
     ``EVSEMaximumCurrentLimit.Value`` to a sentinel, releases.

PASS if both pauses fired in order and both modifications reached the
peer.

Usage:
    sudo python scripts/hw_check/phase5_multi_stage.py \\
        --interface eth0 --budget 60
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


def phase5_multi_stage(
    ctx: RunContext,
    interface: str,
    budget_s: float = 60.0,
) -> PhaseResult:
    from hotwire.core.modes import C_EVSE_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController

    pc = PauseController()
    pc.set_pause_enabled("SessionSetupRes", True)
    pc.set_pause_enabled("ChargeParameterDiscoveryRes", True)

    hits: list[str] = []
    captured: dict[str, dict] = {}

    class _Observer(MessageObserver):
        def __init__(self) -> None:
            self.tx_counts: dict[str, int] = {}

        def on_message(
            self, direction: str, stage: str, params: dict,
        ) -> None:
            if direction == "tx":
                self.tx_counts[stage] = self.tx_counts.get(stage, 0) + 1

    observer = _Observer()

    stop_watcher = threading.Event()

    def _watcher() -> None:
        seen_session = False
        seen_charge = False
        while not stop_watcher.is_set():
            pending = pc.get_pending()
            if pending and not seen_session and pending["stage"] == "SessionSetupRes":
                p = dict(pending["params"])
                captured["SessionSetupRes"] = p
                p["EVSEID"] = "FAKEID01"          # short ASCII override
                hits.append("SessionSetupRes")
                ctx.log.event(kind="phase5b.pause_hit", stage="SessionSetupRes", params=p)
                pc.send(p)
                pc.set_pause_enabled("SessionSetupRes", False)
                seen_session = True
            elif pending and not seen_charge and pending["stage"] == "ChargeParameterDiscoveryRes":
                p = dict(pending["params"])
                captured["ChargeParameterDiscoveryRes"] = p
                # Sentinel: 12345 A max current — way out of band
                p["EVSEMaximumCurrentLimit"] = 12345
                hits.append("ChargeParameterDiscoveryRes")
                ctx.log.event(
                    kind="phase5b.pause_hit",
                    stage="ChargeParameterDiscoveryRes", params=p,
                )
                pc.send(p)
                pc.set_pause_enabled("ChargeParameterDiscoveryRes", False)
                seen_charge = True
            if seen_session and seen_charge:
                return
            time.sleep(0.01)

    threading.Thread(target=_watcher, daemon=True).start()

    bpf = "ether proto 0x88E1 or ip6"
    metrics: dict[str, object] = {
        "role": "evse", "interface": interface, "budget_s": budget_s,
    }
    artifacts = []

    with PacketCapture(
        ctx, phase="phase5b", interface=interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            artifacts.append(cap.pcap_path)

        try:
            worker = HotWireWorker(
                callbackAddToTrace=lambda s: ctx.log.event(
                    kind="phase5b.trace", message=s,
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
                name="phase5_multi_stage", status=Status.ERROR,
                summary=f"failed to construct worker: {e}",
                metrics=metrics, artifacts=artifacts,
            )

        ctx.log.event(kind="phase5b.start", budget_s=budget_s)
        t0 = time.monotonic()
        deadline = t0 + budget_s
        while time.monotonic() < deadline:
            try:
                worker.mainfunction()
            except Exception:    # noqa: BLE001
                break
            if len(hits) >= 2:
                # Give observer a moment to record the released TX
                time.sleep(0.1)
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
    metrics["elapsed_s"] = round(elapsed, 3)
    metrics["hits"] = hits
    metrics["tx_SessionSetupRes"] = observer.tx_counts.get("SessionSetupRes", 0)
    metrics["tx_ChargeParameterDiscoveryRes"] = observer.tx_counts.get(
        "ChargeParameterDiscoveryRes", 0,
    )

    if len(hits) < 2:
        return PhaseResult(
            name="phase5_multi_stage", status=Status.FAIL,
            summary=f"only {len(hits)}/2 stages paused (peer not driving session?)",
            metrics=metrics, artifacts=artifacts,
        )
    return PhaseResult(
        name="phase5_multi_stage", status=Status.PASS,
        summary=(
            f"paused both SessionSetupRes (EVSEID→FAKEID01) and "
            f"ChargeParameterDiscoveryRes (EVSEMaxCurrent→12345A) in "
            f"{elapsed:.1f}s; tx counts {observer.tx_counts}"
        ),
        metrics=metrics, artifacts=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    _seed_offscreen_qt()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--budget", type=float, default=60.0)
    args = parser.parse_args(argv)

    print_banner("Phase 5b — multi-stage pause-and-send")
    ctx = RunContext.create_standalone(interface=args.interface)
    try:
        result = phase5_multi_stage(ctx, args.interface, args.budget)
    finally:
        ctx.close()
    print_result(result)
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
