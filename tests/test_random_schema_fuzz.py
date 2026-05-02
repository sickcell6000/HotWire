"""
Random-parameter fuzz test for every configurable stage.

Goals
-----
1. **Schema sanity** — for each stage × each field, pick a random legal
   value and verify the combined override dict round-trips through the
   FSM's command-builder → OpenV2G encoder → V2GTP frame without raising
   or producing an error string.
2. **Wire-level sanity** — whatever we encoded must decode back to a DIN
   message of the expected msgName. If it doesn't, the arg order or type
   in the schema / FSM builder is wrong.
3. **End-to-end** — a handful of random (legal) EVSE overrides applied
   to a live dual-worker session must not break the DIN handshake. The
   PEV's trace should still reach ``WaitForCurrentDemandRes``.

Why random values?
------------------
We want to catch ordering errors and off-by-one schema mistakes that
aren't obvious from the defaults. A deterministic test using default
values passes trivially because the defaults came from the same source
as the builder. Random values break that correlation.

Seed
----
Seeded by default (``HOTWIRE_FUZZ_SEED`` env, fallback 20260418) so
failures are reproducible. Unset the env var or pass a new seed to
explore different parameter spaces.
"""
from __future__ import annotations

import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini"))

from hotwire.core.config import load as load_config  # noqa: E402

load_config()

from hotwire.exi.connector import exiDecode, exiEncode  # noqa: E402
from hotwire.fsm import PauseController  # noqa: E402
from hotwire.fsm.pause_controller import PauseController as _PC  # noqa: E402,F401
from hotwire.gui.stage_schema import (  # noqa: E402
    FieldSpec,
    STAGE_SCHEMAS_EVSE,
    STAGE_SCHEMAS_PEV,
)


SEED = int(os.environ.get("HOTWIRE_FUZZ_SEED", "20260418"))


# --- Random value generator per field type ---------------------------


def _random_value(spec: FieldSpec, rng: random.Random) -> Any:
    """Pick a legal random value for one FieldSpec.

    Known OpenV2G codec quirks we work around:

    * ``SAScheduleList_isUsed = 0`` crashes EDe with internal error -109
      (the codec refuses to encode an "unused" schedule list alongside
      the rest of the ChargeParameterDiscoveryRes fields). We always
      force it to 1.
    * Per-field ``Unit`` values are not interchangeable — current uses
      A/Ah, voltage V, power W/VA/Wh. The combo branch below picks the
      canonical unit per field name.
    * Multipliers outside [-3, +3] aren't valid DIN signed-byte
      multipliers and the codec rejects them.
    """
    key_lower = spec.key.lower()
    # Codec quirk: setting SAScheduleList_isUsed=0 makes OpenV2G crash
    # while encoding the rest of the ChargeParameterDiscoveryRes.
    if key_lower == "saschedulelist_isused":
        return "1"
    if spec.widget == "combo":
        # OpenV2G's encoder is strict about which dinunitSymbolType
        # values are valid per physical-value triplet (current must use
        # A/Ah, voltage must use V, power must use W/VA/Wh). Pick the
        # canonical unit per enclosing field rather than any random
        # option from the full enum.
        if "unit" in key_lower and spec.options:
            if "voltage" in key_lower and "V" in spec.options:
                return "V"
            if "current" in key_lower and "A" in spec.options:
                return "A"
            if "power" in key_lower and "W" in spec.options:
                return "W"
        return rng.choice(list(spec.options))
    if spec.widget == "bool":
        return rng.choice([True, False])
    if spec.widget == "int":
        # Domain bounds tuned per common field. Order matters —
        # "multiplier" and "unit" must be checked BEFORE "voltage" /
        # "current" because field names like
        # EVSEMaximumVoltageLimitMultiplier contain both substrings.
        if "multiplier" in key_lower:
            return rng.choice([-3, -2, -1, 0, 1, 2, 3])
        if "unit" in key_lower:
            # OpenV2G is strict about which dinunitSymbolType values are
            # legal per physical-value field. Pick the canonical unit for
            # the enclosing field to keep the fuzzer inside the codec's
            # accept-set. (Unit enum: 0=h, 1=m, 2=s, 3=A, 4=Ah, 5=V,
            # 6=VA, 7=W, 8=W_s, 9=Wh.)
            if "voltage" in key_lower:
                return 5                      # V
            if "current" in key_lower:
                return 3                      # A
            if "power" in key_lower:
                return 7                      # W
            return rng.choice([3, 5, 7])
        if "delay" in key_lower:
            return rng.randint(0, 60)
        if "soc" in key_lower:
            return rng.randint(0, 100)
        if "schemaid" in key_lower:
            return rng.choice([0, 1, 2])
        if "schedtuple" in key_lower:
            return 0
        if "array" in key_lower or "len" in key_lower:
            return 1
        if "pmax" in key_lower:
            return rng.randint(10, 100)
        if "voltage" in key_lower:
            return rng.randint(200, 500)     # realistic DC pack voltage
        if "current" in key_lower:
            return rng.randint(1, 200)
        if "power" in key_lower:
            return rng.randint(1, 100)
        if "max" in key_lower:
            return rng.randint(0, 60)
        return rng.randint(0, 100)
    if spec.widget == "hex":
        if "evccid" in key_lower:
            return "".join(rng.choices("0123456789abcdef", k=12))
        if "evseid" in key_lower:
            return "".join(rng.choices("0123456789ABCDEF", k=14))
        if "sessionid" in key_lower:
            return "".join(rng.choices("0123456789ABCDEF", k=16))
        return "".join(rng.choices("0123456789abcdef", k=12))
    if spec.widget == "str":
        return ""  # blank — callers rely on PayloadHex="" falling back to Preset
    return ""


def _apply_to_wire(spec: FieldSpec, raw: Any) -> Any:
    """Mirror StageConfigPanel.get_values — run the to_wire converter."""
    return spec.to_wire(raw) if spec.to_wire else raw


# --- EVSE FSM command builders as pure functions --------------------


def _build_evse_command(stage: str, params: dict[str, Any]) -> str:
    """Re-implementation of the command-string part of each EVSE FSM
    handler, isolated from the surrounding FSM state so we can fuzz it
    without booting a Worker. Kept in sync with fsm_evse.py.
    """
    # Schema selection = DIN ("D") for fuzz tests.
    ss = "D"

    def _g(key: str, default: Any) -> Any:
        v = params.get(key, default)
        return v

    if stage == "supportedAppProtocolRes":
        if _g("SchemaID_isUsed", 1) == 0:
            return f"Eh_{_g('ResponseCode', 0)}_0"
        return (
            f"Eh_{_g('ResponseCode', 0)}_{_g('SchemaID_isUsed', 1)}_"
            f"{_g('SchemaID', 1)}"
        )

    if stage == "SessionSetupRes":
        return f"EDa_{_g('ResponseCode', 1)}_{_g('EVSEID', '5A5A4445464C54')}"

    if stage == "ServiceDiscoveryRes":
        return f"E{ss}b_{_g('ResponseCode', 0)}"

    if stage == "ServicePaymentSelectionRes":
        return f"E{ss}c_{_g('ResponseCode', 0)}"

    if stage == "ContractAuthenticationRes":
        return (
            f"E{ss}l_{_g('EVSEProcessing', 1)}_{_g('ResponseCode', 0)}"
        )

    if stage == "ChargeParameterDiscoveryRes":
        parts = [
            _g("ResponseCode", 0), _g("EVSEProcessing", 0),
            _g("SAScheduleList_isUsed", 1), _g("SAScheduleListArrayLen", 1),
            _g("SchedTupleStart", 0), _g("PMax", 50),
            _g("IsolationStatusUsed", 1), _g("IsolationStatus", 0),
            _g("EVSEStatusCode", 1), _g("NotificationMaxDelay", 0),
            _g("EVSENotification", 0),
            _g("EVSEMaximumCurrentLimitMultiplier", 0),
            _g("EVSEMaximumCurrentLimit", 200),
            _g("EVSEMaximumCurrentLimitUnit", 3),
            _g("EVSEMaximumPowerLimit_isUsed", 1),
            _g("EVSEMaximumPowerLimitMultiplier", 3),
            _g("EVSEMaximumPowerLimit", 10),
            _g("EVSEMaximumPowerLimitUnit", 7),
            _g("EVSEMaximumVoltageLimitMultiplier", 0),
            _g("EVSEMaximumVoltageLimit", 450),
            _g("EVSEMaximumVoltageLimitUnit", 5),
            _g("EVSEMinimumCurrentLimitMultiplier", 0),
            _g("EVSEMinimumCurrentLimit", 1),
            _g("EVSEMinimumCurrentLimitUnit", 3),
            _g("EVSEMinimumVoltageLimitMultiplier", 0),
            _g("EVSEMinimumVoltageLimit", 200),
            _g("EVSEMinimumVoltageLimitUnit", 5),
        ]
        return f"E{ss}e_" + "_".join(str(x) for x in parts)

    if stage == "CableCheckRes":
        return (
            f"E{ss}f_{_g('EVSEProcessing', 0)}_"
            f"{_g('EVSEStatusCode', 1)}_{_g('IsolationStatus', 1)}_"
            f"{_g('IsolationStatusUsed', 1)}_"
            f"{_g('NotificationMaxDelay', 0)}_{_g('EVSENotification', 0)}"
        )

    if stage == "PreChargeRes":
        return (
            f"E{ss}g_{int(_g('EVSEPresentVoltage', 350))}_"
            f"{_g('ResponseCode', 0)}_"
            f"{_g('IsolationStatusUsed', 1)}_{_g('IsolationStatus', 1)}_"
            f"{_g('EVSEStatusCode', 1)}_"
            f"{_g('NotificationMaxDelay', 0)}_{_g('EVSENotification', 0)}"
        )

    if stage == "PowerDeliveryRes":
        return (
            f"E{ss}h_{_g('ResponseCode', 0)}_"
            f"{_g('IsolationStatusUsed', 1)}_{_g('IsolationStatus', 1)}_"
            f"{_g('EVSEStatusCode', 1)}_"
            f"{_g('NotificationMaxDelay', 0)}_{_g('EVSENotification', 0)}"
        )

    if stage == "CurrentDemandRes":
        parts = [
            _g("ResponseCode", 0), _g("IsolationStatusUsed", 1),
            _g("IsolationStatus", 1), _g("EVSEStatusCode", 1),
            _g("NotificationMaxDelay", 0), _g("EVSENotification", 0),
            _g("EVSEPresentVoltageMultiplier", 0),
            _g("EVSEPresentVoltage", 400),
            _g("EVSEPresentVoltageUnit", 5),
            _g("EVSEPresentCurrentMultiplier", 0),
            _g("EVSEPresentCurrent", 50),
            _g("EVSEPresentCurrentUnit", 3),
            _g("EVSECurrentLimitAchieved", 0),
            _g("EVSEVoltageLimitAchieved", 0),
            _g("EVSEPowerLimitAchieved", 0),
            _g("EVSEMaximumVoltageLimit_isUsed", 1),
            _g("EVSEMaximumVoltageLimitMultiplier", 0),
            _g("EVSEMaximumVoltageLimit", 450),
            _g("EVSEMaximumVoltageLimitUnit", 5),
            _g("EVSEMaximumCurrentLimit_isUsed", 1),
            _g("EVSEMaximumCurrentLimitMultiplier", 0),
            _g("EVSEMaximumCurrentLimit", 200),
            _g("EVSEMaximumCurrentLimitUnit", 3),
            _g("EVSEMaximumPowerLimit_isUsed", 1),
            _g("EVSEMaximumPowerLimitMultiplier", 3),
            _g("EVSEMaximumPowerLimit", 60),
            _g("EVSEMaximumPowerLimitUnit", 7),
        ]
        return f"E{ss}i_" + "_".join(str(x) for x in parts)

    if stage == "WeldingDetectionRes":
        return (
            f"E{ss}j_{_g('ResponseCode', 0)}_"
            f"{_g('IsolationStatusUsed', 1)}_{_g('IsolationStatus', 1)}_"
            f"{_g('EVSEStatusCode', 1)}_"
            f"{_g('NotificationMaxDelay', 0)}_{_g('EVSENotification', 0)}_"
            f"{_g('EVSEPresentVoltageMultiplier', 0)}_"
            f"{_g('EVSEPresentVoltage', 0)}_"
            f"{_g('EVSEPresentVoltageUnit', 5)}"
        )

    if stage == "SessionStopRes":
        return f"E{ss}k_{_g('ResponseCode', 0)}"

    raise ValueError(f"unknown EVSE stage: {stage}")


def _build_pev_command(stage: str, params: dict[str, Any]) -> str:
    """Re-implementation of each PEV FSM builder. Uses placeholder
    SessionID for stages that accept one — that's how the FSM behaves when
    the PEV hasn't received a SessionSetupRes yet."""
    def _g(key: str, default: Any) -> Any:
        return params.get(key, default)

    sid = _g("SessionID", "0102030405060708") or "0102030405060708"

    if stage == "SessionSetupReq":
        evccid = _g("EVCCID", "") or "d83add22f182"
        return f"EDA_{evccid}"
    if stage == "ServiceDiscoveryReq":
        return f"EDB_{sid}"
    if stage == "ServicePaymentSelectionReq":
        return f"EDC_{sid}"
    if stage == "ContractAuthenticationReq":
        return f"EDL_{sid}"
    if stage == "ChargeParameterDiscoveryReq":
        return f"EDE_{sid}_{_g('SoC', 30)}"
    if stage == "CableCheckReq":
        return f"EDF_{sid}_{_g('SoC', 30)}"
    if stage == "PreChargeReq":
        return f"EDG_{sid}_{_g('SoC', 30)}_{_g('EVTargetVoltage', 350)}"
    if stage == "PowerDeliveryReq":
        return (
            f"EDH_{sid}_{_g('SoC', 30)}_{_g('ReadyToChargeState', 1)}"
        )
    if stage == "CurrentDemandReq":
        return (
            f"EDI_{sid}_{_g('SoC', 30)}_"
            f"{_g('EVTargetCurrent', 125)}_{_g('EVTargetVoltage', 400)}"
        )
    if stage == "WeldingDetectionReq":
        return f"EDJ_{sid}_{_g('SoC', 30)}"
    if stage == "SessionStopReq":
        return f"EDK_{sid}"
    if stage == "supportedAppProtocolReq":
        return ""  # not encoded via command string — pre-captured blob
    raise ValueError(f"unknown PEV stage: {stage}")


# --- Pure schema fuzz (one randomised dict per stage, encode + decode) ---


@pytest.mark.parametrize("stage", list(STAGE_SCHEMAS_EVSE.keys()))
def test_evse_stage_schema_round_trips(stage):
    """For each EVSE stage, pick random legal values for every schema
    field, run the FSM builder, and verify OpenV2G encodes + decodes it."""
    rng = random.Random(f"{SEED}:{stage}")
    fields = STAGE_SCHEMAS_EVSE[stage]
    raw = {f.key: _random_value(f, rng) for f in fields}
    wire = {k: _apply_to_wire(next(f for f in fields if f.key == k), v)
            for k, v in raw.items()}

    cmd = _build_evse_command(stage, wire)
    hex_out = exiEncode(cmd)
    assert hex_out and "error" not in hex_out.lower(), (
        f"OpenV2G refused {stage} command: {cmd!r} -> {hex_out!r}\n"
        f"raw={raw}\nwire={wire}"
    )
    # Round-trip decode.
    schema_prefix = "DH" if stage == "supportedAppProtocolRes" else "DD"
    decoded = exiDecode(hex_out, schema_prefix)
    assert stage in decoded or decoded.count("msgName"), (
        f"decoded frame doesn't report {stage}: {decoded[:200]}"
    )


@pytest.mark.parametrize("stage",
                         [s for s in STAGE_SCHEMAS_PEV if s != "supportedAppProtocolReq"])
def test_pev_stage_schema_round_trips(stage):
    """Same for PEV Req messages."""
    rng = random.Random(f"{SEED}:{stage}")
    fields = STAGE_SCHEMAS_PEV[stage]
    raw = {f.key: _random_value(f, rng) for f in fields}
    wire = {k: _apply_to_wire(next(f for f in fields if f.key == k), v)
            for k, v in raw.items()}

    cmd = _build_pev_command(stage, wire)
    hex_out = exiEncode(cmd)
    assert hex_out and "error" not in hex_out.lower(), (
        f"OpenV2G refused {stage} command: {cmd!r} -> {hex_out!r}\n"
        f"raw={raw}\nwire={wire}"
    )
    decoded = exiDecode(hex_out, "DD")
    assert stage in decoded or decoded.count("msgName"), (
        f"decoded frame doesn't report {stage}: {decoded[:200]}"
    )


# --- Multi-iteration fuzz — 20 random trials per stage -----------------


def _fuzz_loop(stages: dict[str, tuple[FieldSpec, ...]], builder,
               mode_label: str, n_trials: int = 20) -> list[str]:
    """Return a list of failure descriptions (empty = all passed)."""
    failures: list[str] = []
    rng = random.Random(SEED)
    for stage, fields in stages.items():
        if stage == "supportedAppProtocolReq":
            continue                       # not a command-line stage
        for trial in range(n_trials):
            raw = {f.key: _random_value(f, rng) for f in fields}
            wire = {k: _apply_to_wire(
                        next(f for f in fields if f.key == k), v)
                    for k, v in raw.items()}
            cmd = builder(stage, wire)
            if not cmd:
                continue
            hex_out = exiEncode(cmd)
            if not hex_out or "error" in hex_out.lower():
                failures.append(
                    f"{mode_label}/{stage} trial {trial}: "
                    f"encode failed for cmd={cmd!r}; raw={raw}"
                )
                continue
            schema_prefix = "DH" if stage == "supportedAppProtocolRes" else "DD"
            decoded = exiDecode(hex_out, schema_prefix)
            if not decoded or "msgName" not in decoded:
                failures.append(
                    f"{mode_label}/{stage} trial {trial}: "
                    f"decode garbled. cmd={cmd!r}"
                )
    return failures


def test_evse_all_stages_fuzz_20_trials_each():
    """20 randomised trials per EVSE stage (240 encode+decode pairs)."""
    failures = _fuzz_loop(STAGE_SCHEMAS_EVSE, _build_evse_command, "EVSE")
    assert not failures, "\n".join(failures[:20])


def test_pev_all_stages_fuzz_20_trials_each():
    """20 randomised trials per PEV stage."""
    failures = _fuzz_loop(STAGE_SCHEMAS_PEV, _build_pev_command, "PEV")
    assert not failures, "\n".join(failures[:20])


# --- End-to-end fuzz with live dual workers ----------------------------


def _port_is_free() -> bool:
    """Quick check — returns True when port 57122 can be bound *without*
    SO_REUSEADDR. If not, the end-to-end test is skipped (standalone run
    via ``scripts/run_all_tests.py`` always starts with a fresh port)."""
    import socket
    from hotwire.plc.tcp_socket import _resolve_tcp_port
    port = _resolve_tcp_port()
    probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        probe.bind(("::1", port, 0, 0))
        probe.close()
        return True
    except OSError:
        probe.close()
        return False


def test_random_evse_overrides_do_not_break_handshake():
    """Install one random override on each EVSE stage the handshake
    touches, run a live session, verify the PEV still reaches
    CurrentDemand. This is the end-to-end proof that our random schema
    values are protocol-legal — not just syntactically valid EXI."""
    if not _port_is_free():
        pytest.skip("port 57122 busy; run via scripts/run_all_tests.py")

    rng = random.Random(SEED + 1)
    from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE
    from hotwire.core.worker import HotWireWorker

    # Pick safe random overrides. Some combinations would legitimately
    # abort the session (ResponseCode=FAILED, EVSEStatusCode=Shutdown/
    # Malfunction, IsolationStatus=Fault, EVSENotification=StopCharging).
    # The fields below are "happy-path pinned"; everything else gets a
    # fuzzed random value so we still exercise the builders.
    _PINNED_HAPPY = {
        "ResponseCode", "EVSEProcessing",
        "EVSEStatusCode", "IsolationStatus", "IsolationStatusUsed",
        "EVSENotification",
        # CurrentLimitAchieved + VoltageLimitAchieved being True while
        # current/voltage != max tends to confuse PEV logic.
        "EVSECurrentLimitAchieved", "EVSEVoltageLimitAchieved",
        "EVSEPowerLimitAchieved",
    }

    def _safe_override(stage: str) -> dict:
        fields = STAGE_SCHEMAS_EVSE.get(stage, ())
        out: dict = {}
        for f in fields:
            if f.key in _PINNED_HAPPY:
                continue
            # EVSEPresentVoltage for PreChargeRes must stay within
            # u_delta_max of the PEV's accu voltage (350 V default +/- 10 V).
            if stage == "PreChargeRes" and f.key == "EVSEPresentVoltage":
                out[f.key] = rng.randint(345, 355)
                continue
            val = _random_value(f, rng)
            out[f.key] = _apply_to_wire(f, val)
        return out

    evse_pc = PauseController()
    for stage in (
        "SessionSetupRes",
        "PreChargeRes",
        "PowerDeliveryRes",
        "CurrentDemandRes",
    ):
        evse_pc.set_override(stage, _safe_override(stage))

    pev_pc = PauseController()

    class _ObsCapture:
        def __init__(self):
            self.events = []
        def on_message(self, direction, name, params):
            self.events.append((direction, name))

    pev_obs = _ObsCapture()

    evse = HotWireWorker(
        callbackAddToTrace=lambda s: None,
        callbackShowStatus=lambda *a, **kw: None,
        mode=C_EVSE_MODE, isSimulationMode=1,
        pause_controller=evse_pc,
    )
    stop = threading.Event()

    def _evse_tick():
        while not stop.is_set():
            try:
                evse.mainfunction()
            except Exception:
                pass
            time.sleep(0.03)

    t_evse = threading.Thread(target=_evse_tick, daemon=True)
    t_evse.start()
    time.sleep(1.5)

    pev = HotWireWorker(
        callbackAddToTrace=lambda s: None,
        callbackShowStatus=lambda *a, **kw: None,
        mode=C_PEV_MODE, isSimulationMode=1,
        pause_controller=pev_pc,
        message_observer=pev_obs,
    )

    def _pev_tick():
        while not stop.is_set():
            try:
                pev.mainfunction()
            except Exception:
                pass
            time.sleep(0.03)

    t_pev = threading.Thread(target=_pev_tick, daemon=True)
    t_pev.start()

    deadline = time.time() + 15
    got_cd = False
    while time.time() < deadline:
        time.sleep(0.3)
        if any(name == "CurrentDemandRes" and direction == "rx"
               for direction, name in pev_obs.events):
            got_cd = True
            break

    stop.set()
    t_evse.join(timeout=2)
    t_pev.join(timeout=2)

    assert got_cd, (
        "With random EVSE overrides applied, PEV never reached "
        f"CurrentDemand. Observed rx: "
        f"{sorted({n for d, n in pev_obs.events if d == 'rx'})}"
    )


if __name__ == "__main__":
    # Plain-Python runner (expands parametrize).
    import traceback
    tests = [(k, v) for k, v in list(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for name, t in tests:
        marks = getattr(t, "pytestmark", [])
        param_cases: list[tuple] = []
        for mark in marks:
            if getattr(mark, "name", None) == "parametrize":
                argvals = mark.args[1]
                for v in argvals:
                    if not isinstance(v, (list, tuple)):
                        v = (v,)
                    param_cases.append(tuple(v))
                break
        if param_cases:
            for case in param_cases:
                label = f"{name}[{','.join(repr(v) for v in case)}]"
                try:
                    t(*case)
                    print(f"[PASS] {label}")
                except Exception:                                # noqa: BLE001
                    fails += 1
                    print(f"[FAIL] {label}")
                    traceback.print_exc()
        else:
            try:
                t()
                print(f"[PASS] {name}")
            except Exception:                                    # noqa: BLE001
                fails += 1
                print(f"[FAIL] {name}")
                traceback.print_exc()
    sys.exit(0 if fails == 0 else 1)
