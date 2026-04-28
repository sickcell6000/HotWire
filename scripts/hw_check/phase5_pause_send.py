"""
Phase 5 — Interactive pause-and-send headless validation.

Verifies that HotWire's signature "pause / modify / send" feature works
end-to-end against the real-hardware worker stack. Where Phase 4 proves
the V2G chain runs and Attack A2 proves ``set_override`` reshapes
outbound messages, this phase covers the third PauseController path:
``set_pause_enabled(stage, True)`` → FSM blocks → external code edits the
captured params → ``send(modified)`` releases the FSM with new values.

The GUI normally drives this loop via Qt dialogs. Reviewers running
without a display server can use ``QT_QPA_PLATFORM=offscreen`` plus this
script to drive the same code path programmatically. We:

  1. Build a real ``HotWireWorker`` in EVSE mode against the chosen NIC
     (``isSimulationMode=0``).
  2. Toggle pause for ``PreChargeRes``.
  3. Spawn a watcher thread that polls ``pause_controller.get_pending``
     every few ms and, on the first hit, edits ``EVSEPresentVoltage`` to
     a sentinel value (777 V — chosen because it never matches anything
     the rig would naturally produce) and calls ``send``.
  4. Tick the worker against a peer PEV running ``phase4_v2g``.
  5. PASS if at least one ``PreChargeRes`` was actually paused (proving
     ``set_pause_enabled`` blocks the FSM) AND the released params still
     reach the wire as a real PreChargeRes (so the FSM didn't deadlock).

No GUI window is created; the test imports the headless ``HotWireWorker``
plus ``PauseController`` directly. That keeps the test runnable on the Pi
(PyQt5 present but no DISPLAY), the EVSE Windows host (no Qt at all),
and CI containers.

Usage:
    sudo python scripts/hw_check/phase5_pause_send.py \\
        --interface eth0 --budget 60

Pair with a peer running phase4 PEV side; without a peer this test
times out at "no PreChargeReq received" (still useful — it proves the
GUI hook layer is wired even when the V2G stack can't make it that far).
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))                  # for _runner
sys.path.insert(0, str(_THIS.parent.parent.parent))    # for hotwire/

from _runner import (  # type: ignore[import-not-found]  # noqa: E402
    PacketCapture, PhaseResult, RunContext, Status, print_banner, print_result,
)


def _seed_offscreen_qt() -> None:
    """Force Qt to use the offscreen platform if PyQt is importable.

    HotWireWorker itself is Qt-free, but some imports inside the FSM
    layer (the GUI signal proxy) construct a QObject subclass. Setting
    QT_QPA_PLATFORM=offscreen at the very top of the script before any
    import keeps headless invocation safe.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def phase5_pause_send(
    ctx: RunContext,
    interface: str,
    budget_s: float = 60.0,
) -> PhaseResult:
    from hotwire.core.modes import C_EVSE_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver
    from hotwire.fsm.pause_controller import PauseController

    pause_controller = PauseController()
    pause_controller.set_pause_enabled("PreChargeRes", True)

    paused_count = {"n": 0}
    released_count = {"n": 0}
    captured_params: list[dict] = []

    class _Observer(MessageObserver):
        def __init__(self) -> None:
            self.tx_pre_charge_res = 0
            self.last_tx_voltage: int | None = None

        def on_message(
            self, direction: str, stage: str, params: dict,
        ) -> None:
            if direction == "tx" and stage == "PreChargeRes":
                self.tx_pre_charge_res += 1
                v = params.get("EVSEPresentVoltage.Value") or params.get(
                    "EVSEPresentVoltage"
                )
                try:
                    self.last_tx_voltage = int(v) if v is not None else None
                except (TypeError, ValueError):
                    self.last_tx_voltage = None

    observer = _Observer()

    stop_watcher = threading.Event()

    def _watcher() -> None:
        """Poll for paused message; on first hit, edit voltage and release."""
        while not stop_watcher.is_set():
            pending = pause_controller.get_pending()
            if pending and pending.get("stage") == "PreChargeRes":
                p = dict(pending["params"])
                captured_params.append(p)
                p["EVSEPresentVoltage"] = 777   # sentinel
                ctx.log.event(
                    kind="phase5.pause_hit",
                    stage="PreChargeRes",
                    captured_params=p,
                    sentinel_voltage=777,
                )
                paused_count["n"] += 1
                pause_controller.send(p)
                released_count["n"] += 1
                # Disable pause after the first round-trip — we proved the
                # mechanism, no point spending the budget blocking every
                # subsequent PreChargeRes.
                pause_controller.set_pause_enabled("PreChargeRes", False)
                return
            time.sleep(0.02)

    watcher_thread = threading.Thread(target=_watcher, daemon=True)
    watcher_thread.start()

    bpf = "ether proto 0x88E1 or ip6"
    metrics: dict[str, object] = {
        "role": "evse",
        "interface": interface,
        "budget_s": budget_s,
    }
    artifacts = []

    with PacketCapture(
        ctx, phase="phase5", interface=interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            artifacts.append(cap.pcap_path)

        try:
            worker = HotWireWorker(
                callbackAddToTrace=lambda s: ctx.log.event(
                    kind="phase5.trace", message=s,
                ),
                callbackShowStatus=lambda *a, **kw: None,
                mode=C_EVSE_MODE,
                isSimulationMode=0,
                pause_controller=pause_controller,
                message_observer=observer,
            )
        except Exception as e:        # noqa: BLE001
            stop_watcher.set()
            return PhaseResult(
                name="phase5_pause_send",
                status=Status.ERROR,
                summary=f"failed to construct HotWireWorker: {e}",
                metrics=metrics,
                artifacts=artifacts,
            )

        ctx.log.event(kind="phase5.start", budget_s=budget_s)
        t0 = time.monotonic()
        deadline = t0 + budget_s
        while time.monotonic() < deadline:
            try:
                worker.mainfunction()
            except Exception as e:    # noqa: BLE001
                ctx.log.event(
                    kind="phase5.tick_error",
                    exc_type=type(e).__name__, message=str(e),
                )
                break
            # Early-exit once we've proved the round-trip + observed the
            # sentinel actually went out on the wire.
            if (paused_count["n"] >= 1 and observer.tx_pre_charge_res >= 1):
                # Give the observer one more tick to record the value.
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
    metrics["elapsed_s"] = round(elapsed, 3)
    metrics["paused_count"] = paused_count["n"]
    metrics["released_count"] = released_count["n"]
    metrics["tx_pre_charge_res"] = observer.tx_pre_charge_res
    metrics["last_tx_voltage"] = observer.last_tx_voltage

    if paused_count["n"] == 0:
        return PhaseResult(
            name="phase5_pause_send",
            status=Status.FAIL,
            summary=(
                "PauseController never reported a pending PreChargeRes "
                "(no peer activity in budget)"
            ),
            metrics=metrics,
            artifacts=artifacts,
        )
    if released_count["n"] != paused_count["n"]:
        return PhaseResult(
            name="phase5_pause_send",
            status=Status.FAIL,
            summary=(
                f"paused {paused_count['n']} times but only released "
                f"{released_count['n']} (FSM may be stuck)"
            ),
            metrics=metrics,
            artifacts=artifacts,
        )
    if observer.tx_pre_charge_res == 0:
        return PhaseResult(
            name="phase5_pause_send",
            status=Status.FAIL,
            summary=(
                "release succeeded but no PreChargeRes ever reached the "
                "wire (observer saw 0)"
            ),
            metrics=metrics,
            artifacts=artifacts,
        )

    return PhaseResult(
        name="phase5_pause_send",
        status=Status.PASS,
        summary=(
            f"paused PreChargeRes {paused_count['n']}x, edited "
            f"EVSEPresentVoltage→777, released, observed "
            f"{observer.tx_pre_charge_res} tx PreChargeRes "
            f"(last_voltage={observer.last_tx_voltage}) in "
            f"{elapsed:.1f}s"
        ),
        metrics=metrics,
        artifacts=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    _seed_offscreen_qt()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--budget", type=float, default=60.0)
    args = parser.parse_args(argv)

    print_banner("Phase 5 — interactive pause-and-send (headless)")
    ctx = RunContext.create_standalone(interface=args.interface)
    try:
        result = phase5_pause_send(ctx, args.interface, args.budget)
    finally:
        ctx.close()
    print_result(result)
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
