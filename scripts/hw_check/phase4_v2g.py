"""
Phase 4 — End-to-end DIN 70121 session.

The finisher. Everything up to here (link, SLAC, SDP) is a
prerequisite for this exchange — so when phases 1-3 all PASS but
phase 4 FAILs, you've got a pure V2G-layer bug rather than a
cabling / modem / IPv6 issue.

How it runs:

1. Spin up a full :class:`HotWireWorker` in the chosen role (EVSE or
   PEV) backed by the real hardware interface (``isSimulationMode=0``
   is the difference from the existing ``scripts/run_evse.py``).
2. Tick it for the configured budget, tracking every stage the FSM
   enters and every message the :class:`MessageObserver` sees.
3. PASS if:
   * PEV role  — CurrentDemand loop fires at least ``--min-cd``
     messages (default 5, matching the ~300 ms cycle of production EVs)
   * EVSE role — SessionSetup + ChargeParameterDiscovery + PowerDelivery
     were all observed in the outbound direction
4. Always record a pcap across the whole exchange so a failed session
   produces a Wireshark-ready artifact.
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

from _runner import (  # noqa: E402
    PacketCapture,
    PhaseResult,
    RunContext,
    Status,
    print_banner,
    print_result,
    run_phase,
)


def phase4_v2g(
    ctx: RunContext,
    role: str,
    budget_s: float = 60.0,
    min_current_demand: int = 5,
    pause_controller=None,
) -> PhaseResult:
    """End-to-end DIN session driver.

    ``pause_controller`` accepts an externally-built
    :class:`hotwire.fsm.pause_controller.PauseController` so callers can
    install :meth:`set_override` hooks before the worker starts. Useful
    for attack matrix harnesses (``phase4_v2g_with_attack.py``) that
    need the same V2G stack but with attacker-shaped messages on the
    wire. When omitted the worker constructs a fresh empty controller —
    same behaviour as before.
    """
    if not ctx.interface:
        return PhaseResult(
            name="phase4_v2g",
            status=Status.SKIP,
            summary="no interface configured (simulation-only run)",
        )

    # Late-bind to avoid importing Qt / the full FSM when the phase is
    # skipped.
    os.environ.setdefault(
        "HOTWIRE_CONFIG",
        str(_THIS.parent.parent.parent / "config" / "hotwire.ini"),
    )
    from hotwire.core.config import load as _load_config
    _load_config()
    from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE
    from hotwire.core.worker import HotWireWorker
    from hotwire.fsm.message_observer import MessageObserver

    mode = {"pev": C_PEV_MODE, "evse": C_EVSE_MODE}[role]

    # --- Observer: record every wire-level message the FSM emits / receives
    observed: list[tuple[str, str]] = []   # (direction, stage_name)
    current_demand_seen = 0

    # Paper-claim-relevant fields whose ACTUAL VALUES we record into
    # the session.jsonl (not just the key names). Reviewers checking
    # AEC artifact evidence need these to confirm e.g. "EVCCID was
    # transmitted in plaintext = d83add22f182" without firing up an
    # EXI decoder against the pcap. None of these fields are PII —
    # they're protocol-level identifiers and physical-channel state.
    _AEC_RECORDED_FIELDS = (
        "EVCCID",                                # paper §4.1 A1 evidence
        "EVSEID",                                # paper §6.1 station identity
        "EVTargetVoltage", "EVTargetVoltage.Value",
        "EVTargetCurrent", "EVTargetCurrent.Value",
        "EVSEPresentVoltage", "EVSEPresentVoltage.Value",
        "EVSEPresentCurrent", "EVSEPresentCurrent.Value",
        "DC_EVStatus.EVRESSSOC",                # SoC %
        "DC_EVSEStatus.EVSEStatusCode",
        "EVSEStatusCode_text",
        "ResponseCode",
        "EVSEProcessing",
        "ChargingComplete",
        "header.SessionID",
    )

    class _Observer(MessageObserver):
        def on_message(self, direction, stage, params) -> None:
            nonlocal current_demand_seen
            observed.append((direction, stage))
            recorded = {
                k: params[k] for k in _AEC_RECORDED_FIELDS if k in params
            }
            ctx.log.event(
                kind="phase4.message", direction=direction,
                stage=stage,
                params_keys=list(params.keys()),
                values=recorded,
            )
            if direction == "rx" and stage.startswith("CurrentDemand"):
                current_demand_seen += 1

    observer = _Observer()

    traces: list[str] = []

    def _trace(s: str) -> None:
        traces.append(s)
        ctx.log.event(kind="phase4.trace", message=s)

    def _status(key: str, value: str = "") -> None:
        ctx.log.event(kind="phase4.status", key=key, value=value)

    bpf = "ether proto 0x88E1 or ip6"
    metrics: dict[str, object] = {
        "role": role, "interface": ctx.interface, "budget_s": budget_s,
        "min_current_demand": min_current_demand,
    }
    artifacts: list[Path] = []

    with PacketCapture(
        ctx, phase="phase4", interface=ctx.interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            artifacts.append(cap.pcap_path)

        # Pre-pair the local modem with the stable NMK/NID from config
        # *before* standing up the full worker. Disabled by default
        # because the worker's own ``RealHomePlug._ensure_slac_started``
        # already pulls the same config NMK/NID and runs SLAC + CM_SET_KEY
        # idempotently — running it twice creates a race where the modem
        # locks into the prepair AVLN and ignores the worker's fresh
        # M-SOUNDs. Set ``HOTWIRE_PHASE4_PREPAIR=1`` to re-enable for
        # flaky modems where AVLN drift recovery is needed.
        if os.environ.get("HOTWIRE_PHASE4_PREPAIR", "0") == "1":
            _trace(
                "[phase4] HOTWIRE_PHASE4_PREPAIR=1 — running pre-pair SLAC"
            )
            _prepair_modem(ctx, role, _trace)
        else:
            _trace(
                "[phase4] pre-pair disabled (worker handles SLAC); "
                "set HOTWIRE_PHASE4_PREPAIR=1 to opt in"
            )

        try:
            worker_kwargs: dict = dict(
                callbackAddToTrace=_trace,
                callbackShowStatus=_status,
                mode=mode,
                isSimulationMode=0,           # <- real hardware
                message_observer=observer,
            )
            if pause_controller is not None:
                worker_kwargs["pause_controller"] = pause_controller
            worker = HotWireWorker(**worker_kwargs)
        except Exception as e:                                  # noqa: BLE001
            ctx.log.event(
                kind="phase4.worker_error",
                exc_type=type(e).__name__, message=str(e),
            )
            return PhaseResult(
                name="phase4_v2g",
                status=Status.ERROR,
                summary=f"failed to construct HotWireWorker: {e}",
                metrics=metrics,
                artifacts=artifacts,
            )

        ctx.log.event(kind="phase4.start", role=role, budget_s=budget_s)
        t0 = time.monotonic()
        deadline = t0 + budget_s
        while time.monotonic() < deadline:
            try:
                worker.mainfunction()
            except Exception as e:                              # noqa: BLE001
                ctx.log.event(
                    kind="phase4.tick_error",
                    exc_type=type(e).__name__, message=str(e),
                )
                break
            # Early-exit if the phase already satisfies the success
            # criteria — saves the rest of the budget.
            if _early_pass(role, observed, current_demand_seen, min_current_demand):
                break
            time.sleep(0.03)
        worker_stop(worker)

    elapsed = time.monotonic() - t0 if 't0' in dir() else budget_s
    metrics["elapsed_s"] = round(elapsed, 3)
    metrics["trace_lines"] = len(traces)
    metrics["total_messages"] = len(observed)
    metrics["current_demand_count"] = current_demand_seen

    stage_counts: dict[str, int] = {}
    for direction, stage in observed:
        key = f"{direction}.{stage}"
        stage_counts[key] = stage_counts.get(key, 0) + 1
    for k, v in sorted(stage_counts.items()):
        metrics[f"msg.{k}"] = v

    details = _format_details(observed, traces)

    status, summary = _verdict(
        role, observed, current_demand_seen, min_current_demand, elapsed,
    )

    return PhaseResult(
        name="phase4_v2g",
        status=status,
        summary=summary,
        details=details,
        metrics=metrics,
        artifacts=artifacts,
    )


# --- helpers ----------------------------------------------------------


def _prepair_modem(ctx: RunContext, role: str, trace) -> None:
    """Run a short SLAC exchange with the config-pinned NMK/NID so the
    modems land in a known AVLN before the full worker starts.

    This is a no-op when ``plc_nmk_hex`` / ``plc_nid_hex`` are absent
    from hotwire.ini — the worker's own SLAC round will still try on
    its own. We swallow every error; this step is best-effort.

    Budget is deliberately short (8 s) so a dead modem doesn't block
    the real phase that follows. If pre-pair times out we still log
    that fact and let the worker take another crack at it.
    """
    try:
        from hotwire.core.config import getConfigValue
        try:
            nmk_hex = getConfigValue("plc_nmk_hex")
            nid_hex = getConfigValue("plc_nid_hex")
        except SystemExit:
            return
        if not nmk_hex or not nid_hex:
            return
        if len(nmk_hex) != 32 or len(nid_hex) != 14:
            trace(
                "[phase4/prepair] plc_nmk_hex/plc_nid_hex wrong length; "
                "skipping"
            )
            return
        nmk_bytes = bytes.fromhex(nmk_hex)
        nid_bytes = bytes.fromhex(nid_hex)
    except (ValueError, ImportError) as e:
        trace(f"[phase4/prepair] config read failed: {e}")
        return

    # Resolve local MAC via the address manager — same path the worker
    # uses, so we can't disagree about which NIC is "the" PLC. Using
    # ``_resolve_local_mac`` from phase2_slac has historically returned
    # ``None`` when imported through a different sys.path than the one
    # phase2 was designed for.
    try:
        from hotwire.core.address_manager import addressManager
    except ImportError as e:
        trace(f"[phase4/prepair] addressManager import failed: {e}")
        return
    am = addressManager(isSimulationMode=0)
    try:
        am.findLocalMacAddress()
        am.findLinkLocalIpv6Address()
    except Exception as e:                                      # noqa: BLE001
        trace(f"[phase4/prepair] addressManager bring-up failed: {e}")
        return
    raw_mac = am.getLocalMacAddress()
    try:
        local_mac = bytes(raw_mac) if raw_mac is not None else b""
    except (TypeError, ValueError):
        local_mac = b""
    if len(local_mac) != 6:
        trace(
            "[phase4/prepair] addressManager has no usable MAC for "
            f"{ctx.interface}; skipping"
        )
        return

    try:
        from hotwire.plc.l2_transport import PcapL2Transport
        from hotwire.plc.slac import (
            SlacStateMachine, ROLE_EVSE, ROLE_PEV,
        )
    except ImportError as e:
        trace(f"[phase4/prepair] import failed: {e}")
        return

    try:
        transport = PcapL2Transport(ctx.interface)
    except Exception as e:                                      # noqa: BLE001
        trace(f"[phase4/prepair] pcap open failed: {e}")
        return

    sm_role = ROLE_PEV if role == "pev" else ROLE_EVSE
    sm = SlacStateMachine(
        role=sm_role,
        transport=transport,
        local_mac=local_mac,
        callback_add_to_trace=lambda s: trace(f"[phase4/prepair] {s}"),
        nmk=nmk_bytes,
        nid=nid_bytes,
    )
    sm._total_timeout_s = 8.0                                   # noqa: SLF001

    t0 = time.monotonic()
    while time.monotonic() - t0 < 9.0:
        sm.tick()
        if sm.is_paired() or sm.has_failed():
            break
        time.sleep(0.02)

    try:
        transport.close()
    except Exception:                                           # noqa: BLE001
        pass

    status = (
        "paired" if sm.is_paired()
        else "failed" if sm.has_failed()
        else "timeout"
    )
    trace(
        f"[phase4/prepair] done ({status}) in "
        f"{time.monotonic() - t0:.1f}s"
    )


def worker_stop(worker) -> None:
    """Best-effort worker shutdown. Older worker builds may not have
    the ``shutdown`` hook Checkpoint 11 added."""
    for attr in ("shutdown", "stop", "close"):
        fn = getattr(worker, attr, None)
        if callable(fn):
            try:
                fn()
            except Exception:                                   # noqa: BLE001
                pass
            return


def _early_pass(
    role: str,
    observed: list[tuple[str, str]],
    cd_count: int,
    min_cd: int,
) -> bool:
    stages = {stage for _d, stage in observed}
    if role == "pev":
        return cd_count >= min_cd
    # EVSE: three canonical outbound milestones must all be seen, AND
    # we must service at least ``min_cd`` CurrentDemandRes responses so
    # the peer PEV has time to satisfy its own pass criterion before we
    # close the socket. Without this second clause EVSE early-exits in
    # ~5 s and the PEV's CurrentDemand loop never gets the responses it
    # needs to reach min_cd.
    tx_stages = {s for d, s in observed if d == "tx"}
    required = {"SessionSetupRes", "ChargeParameterDiscoveryRes",
                "PowerDeliveryRes"}
    if not required.issubset(tx_stages):
        return False
    cd_responses = sum(
        1 for d, s in observed if d == "tx" and s == "CurrentDemandRes"
    )
    return cd_responses >= min_cd


def _verdict(
    role: str,
    observed: list[tuple[str, str]],
    cd_count: int,
    min_cd: int,
    elapsed: float,
) -> tuple[Status, str]:
    tx_stages = {s for d, s in observed if d == "tx"}
    rx_stages = {s for d, s in observed if d == "rx"}
    if role == "pev":
        if cd_count >= min_cd:
            return (Status.PASS,
                    f"PEV received {cd_count} CurrentDemandRes in {elapsed:.1f}s")
        return (Status.FAIL,
                f"PEV only saw {cd_count}/{min_cd} CurrentDemandRes messages "
                f"(stages reached rx: {sorted(rx_stages)})")
    required = {"SessionSetupRes", "ChargeParameterDiscoveryRes",
                "PowerDeliveryRes"}
    missing = required - tx_stages
    cd_responses = sum(
        1 for d, s in observed if d == "tx" and s == "CurrentDemandRes"
    )
    if missing:
        return (Status.FAIL,
                f"EVSE missing {sorted(missing)} (saw tx: {sorted(tx_stages)})")
    if cd_responses >= min_cd:
        return (Status.PASS,
                f"EVSE served full DIN chain + {cd_responses} "
                f"CurrentDemandRes in {elapsed:.1f}s")
    # Backward-compat: if the operator deliberately set --min-cd 0 we
    # accept the old 'just got to PowerDeliveryRes' behaviour.
    if min_cd == 0:
        return (Status.PASS,
                f"EVSE emitted full DIN response chain in {elapsed:.1f}s")
    return (Status.FAIL,
            f"EVSE only served {cd_responses}/{min_cd} CurrentDemandRes "
            f"messages (full chain reached at "
            f"{elapsed:.1f}s but PEV CurrentDemand loop didn't accumulate)")


def _format_details(observed: list[tuple[str, str]], traces: list[str]) -> str:
    parts: list[str] = []
    parts.append("STAGE SEQUENCE:")
    for d, s in observed[-40:]:
        parts.append(f"  {d:2s} {s}")
    parts.append("")
    parts.append("TRACE TAIL:")
    parts.extend(traces[-30:])
    return "\n".join(parts)


# --- CLI entrypoint ---------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--role", choices=("pev", "evse"), required=True)
    parser.add_argument("--budget", type=float, default=60.0,
                        help="Max session duration in seconds.")
    parser.add_argument("--min-cd", type=int, default=5,
                        help="PEV: minimum CurrentDemandRes count for PASS.")
    args = parser.parse_args(argv)
    print_banner(f"Phase 4 — end-to-end DIN session ({args.role})")
    ctx = RunContext.create_standalone(interface=args.interface)
    result = run_phase(
        ctx, "phase4_v2g", phase4_v2g,
        role=args.role, budget_s=args.budget,
        min_current_demand=args.min_cd,
    )
    print_result(result)
    print(f"\nArtifacts: {ctx.run_dir}")
    ctx.close()
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
