"""
DIN TS 70121:2024-11 conformance checks.

Each test here pins down a specific statement in the standard and fails
if the HotWire implementation drifts away. Citations below use the §/
table number + the V2G-DC- requirement ID (e.g. V2G-DC-226) as printed
in the 2024-11 English revision.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini"))

from hotwire.core.config import load as load_config  # noqa: E402

load_config()

from hotwire.exi.connector import (  # noqa: E402
    addV2GTPHeader,
    exiDecode,
    exiEncode,
)
from hotwire.fsm import PauseController  # noqa: E402
from hotwire.fsm.din_spec import (  # noqa: E402
    APP_HAND_RC_FAILED_NO_NEGOTIATION,
    TCP_PORT_MAX,
    TCP_PORT_MIN,
    V2G_EVCC_CABLE_CHECK_TIMEOUT_S,
    V2G_EVCC_COMMUNICATION_SETUP_TIMEOUT_S,
    V2G_EVCC_MSG_TIMEOUT_CURRENT_DEMAND_S,
    V2G_EVCC_MSG_TIMEOUT_DEFAULT_S,
    V2G_EVCC_PRE_CHARGE_TIMEOUT_S,
    V2G_EVCC_READY_TO_CHARGE_TIMEOUT_S,
    V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S,
    V2G_EVCC_SEQUENCE_TIMEOUT_S,
    seconds_to_cycles,
)
from hotwire.plc.tcp_socket import _resolve_tcp_port  # noqa: E402


# =====================================================================
# §8.7.3  V2GTP header
# =====================================================================


def test_v2gtp_header_has_correct_version_and_inverse_bytes():
    """§8.7.3 Table 15: byte[0]=0x01, byte[1]=0xFE (inverse of 0x01)."""
    payload = bytes.fromhex("ABCD")
    frame = addV2GTPHeader(payload)
    assert frame[0] == 0x01
    assert frame[1] == 0xFE


def test_v2gtp_header_payload_type_is_exi():
    """§8.7.3 Table 16: EXI payload type. HotWire uses 0x8001 (matches the
    V2G-DC-166 note's wording) and the whole pyPLC / OpenV2G ecosystem."""
    payload = bytes.fromhex("ABCD")
    frame = addV2GTPHeader(payload)
    payload_type = (frame[2] << 8) | frame[3]
    assert payload_type == 0x8001


def test_v2gtp_header_length_is_big_endian_32bit():
    """§8.7.3 Table 15: payload length is 4 bytes, network (big-endian)."""
    payload = b"\xde\xad\xbe\xef"    # 4 bytes
    frame = addV2GTPHeader(payload)
    length_be = int.from_bytes(frame[4:8], "big")
    assert length_be == 4


# =====================================================================
# §8.7.2  TCP ports
# =====================================================================


def test_tcp_port_falls_in_iana_dynamic_range():
    """§8.7.2 Table 14: the chosen port must sit in 49152-65535."""
    port = _resolve_tcp_port()
    assert TCP_PORT_MIN <= port <= TCP_PORT_MAX, (
        f"port {port} is outside the IANA dynamic range"
    )


# =====================================================================
# §9.2  Supported-app-protocol negotiation
# =====================================================================


def test_failed_no_negotiation_response_code_value():
    """§9.2 Table 24: Failed_NoNegotiation has enum value 2."""
    assert APP_HAND_RC_FAILED_NO_NEGOTIATION == 2


def test_failed_no_negotiation_encoding_omits_schema_id():
    """V2G-DC-226: when we respond Failed_NoNegotiation we SHALL NOT
    include a SchemaID field. OpenV2G encodes this via SchemaID_isUsed=0.
    The appHandshake decoder reports the fields as a space-separated
    summary in the ``result`` string, so we grep there.
    """
    hex_out = exiEncode(
        f"Eh_{APP_HAND_RC_FAILED_NO_NEGOTIATION}_0"
    )
    assert hex_out and "error" not in hex_out.lower()
    decoded = exiDecode(hex_out, "DH")
    parsed = json.loads(decoded)
    assert parsed.get("msgName") == "supportedAppProtocolRes"
    summary = parsed.get("result", "")
    # ResponseCode must equal the Failed_NoNegotiation enum value (2).
    assert "ResponseCode 2" in summary, (
        f"Failed_NoNegotiation (enum 2) not set; got: {summary}"
    )
    # And SchemaID_isUsed must be 0 per V2G-DC-226.
    assert "SchemaID_isUsed 0" in summary, (
        f"SchemaID_isUsed should be 0 when no negotiation; got: {summary}"
    )


def test_evse_fsm_responds_failed_no_negotiation_when_no_common_protocol():
    """Integration: when the EVSE FSM sees a supportedAppProtocolReq that
    offers a protocol we don't know, it must send Failed_NoNegotiation and
    terminate (not pick a random SchemaID and hope)."""
    from hotwire.fsm import fsm_evse as mod

    f = mod.fsmEvse.__new__(mod.fsmEvse)
    f.callbackAddToTrace = lambda s: None
    f.callbackShowStatus = lambda *a, **kw: None
    f.pause_controller = PauseController()
    f.message_observer = None
    f.state = mod.STATE_WAIT_APP_HANDSHAKE
    f.cyclesInState = 0
    f.rxData = b"\x01\xfe\x80\x01\x00\x00\x00\x01\x00"  # dummy V2GTP
    f.schemaSelection = "D"
    f.blChargeStopTrigger = False
    f.evccid = ""

    # Capture what the FSM builds.
    captured_cmd: list[str] = []

    class _Tcp:
        def transmit(self, msg):
            return 0
    f.Tcp = _Tcp()

    fake_decoded = (
        '{"msgName": "supportedAppProtocolReq",'
        '"AppProtocol_arrayLen": "1",'
        '"NameSpace_0": "urn:unknown:protocol:42",'
        '"SchemaID_0": "5"}'
    )
    orig_decode = mod.exiDecode
    orig_encode = mod.exiEncode
    orig_v2g = mod.addV2GTPHeader
    mod.exiDecode = lambda *a, **kw: fake_decoded

    def _spy_encode(cmd: str) -> str:
        captured_cmd.append(cmd)
        return "deadbeef"
    mod.exiEncode = _spy_encode
    mod.addV2GTPHeader = lambda b: (
        bytearray(b"\x00\x00\x00\x00")
        + (bytearray.fromhex(b) if isinstance(b, str) else bytearray(b))
    )

    try:
        mod.fsmEvse._state_wait_app_handshake(f)
    finally:
        mod.exiDecode = orig_decode
        mod.exiEncode = orig_encode
        mod.addV2GTPHeader = orig_v2g

    assert captured_cmd, "FSM never sent a supportedAppProtocolRes"
    cmd = captured_cmd[0]
    # The Failed_NoNegotiation path uses Eh_2_0 (no SchemaID field).
    assert cmd.startswith("Eh_2_0"), (
        f"expected Eh_2_0... (Failed_NoNegotiation, no SchemaID); got {cmd}"
    )
    # And the FSM should enter the Stopped state so it doesn't continue
    # negotiating on a session it already rejected.
    assert f.state == mod.STATE_STOPPED


# =====================================================================
# §9.4  Mandatory message fields
# =====================================================================


def test_precharge_req_includes_ev_target_current():
    """§9.4.2.5 Table 45: PreChargeReq MUST include EVTargetCurrent.
    HotWire's EDG command produces a wire frame with EVTargetCurrent
    present (codec default 1 A)."""
    hex_out = exiEncode("EDG_0102030405060708_30_350")
    assert hex_out and "error" not in hex_out.lower()
    decoded = exiDecode(hex_out, "DD")
    parsed = json.loads(decoded)
    assert "EVTargetCurrent.Value" in parsed, (
        "EVTargetCurrent missing from PreChargeReq — violates Table 45"
    )


def test_current_demand_res_includes_all_three_max_limits():
    """§9.4.2.8 Table 48 + V2G-DC-948/949/950: CurrentDemandRes MUST
    include EVSEMaximumVoltageLimit, EVSEMaximumCurrentLimit,
    EVSEMaximumPowerLimit (all three are mandatory in DIN, unlike ISO)."""
    # Build a CurrentDemandRes via our 27-arg EDi form (mirror of what
    # fsmEvse._state_wait_flexible builds). All _isUsed flags = 1.
    parts = [
        "0", "1", "1", "1", "0", "0",           # rc + DC_EVSEStatus
        "0", "400", "5",                          # PresentVoltage
        "0", "50", "3",                           # PresentCurrent
        "0", "0", "0",                            # LimitAchieved flags
        "1", "0", "450", "5",                     # MaxVoltage
        "1", "0", "200", "3",                     # MaxCurrent
        "1", "3", "60", "7",                      # MaxPower
    ]
    cmd = "EDi_" + "_".join(parts)
    hex_out = exiEncode(cmd)
    assert hex_out and "error" not in hex_out.lower()
    decoded = exiDecode(hex_out, "DD")
    parsed = json.loads(decoded)
    assert "EVSEMaximumVoltageLimit.Value" in parsed, \
        "EVSEMaximumVoltageLimit missing — V2G-DC-948 violation"
    assert "EVSEMaximumCurrentLimit.Value" in parsed, \
        "EVSEMaximumCurrentLimit missing — V2G-DC-949 violation"
    assert "EVSEMaximumPowerLimit.Value" in parsed, \
        "EVSEMaximumPowerLimit missing — V2G-DC-950 violation"


# =====================================================================
# §9.5.3  DC_EVSEStatus mandatory fields
# =====================================================================


def test_cable_check_res_explicit_notification_fields():
    """§9.5.3 Table 66: DC_EVSEStatus.NotificationMaxDelay and
    DC_EVSEStatus.EVSENotification are mandatory. HotWire's CableCheckRes
    now sends them explicitly (6-arg EDf instead of 4-arg)."""
    cmd = "EDf_0_1_1_1_5_2"  # proc, sc, iso, isoUsed, delay, notif
    hex_out = exiEncode(cmd)
    decoded = exiDecode(hex_out, "DD")
    parsed = json.loads(decoded)
    assert parsed["DC_EVSEStatus.NotificationMaxDelay"] == "5"
    assert parsed["DC_EVSEStatus.EVSENotification"] == "2"


def test_precharge_res_explicit_notification_fields():
    """§9.5.3 Table 66: same for PreChargeRes (7-arg EDg)."""
    cmd = "EDg_350_0_1_1_1_7_1"  # V, rc, isoUsed, isoStatus, sc, delay, notif
    hex_out = exiEncode(cmd)
    decoded = exiDecode(hex_out, "DD")
    parsed = json.loads(decoded)
    assert parsed["DC_EVSEStatus.NotificationMaxDelay"] == "7"
    assert parsed["DC_EVSEStatus.EVSENotification"] == "1"


def test_power_delivery_res_explicit_notification_fields():
    """§9.5.3 Table 66: same for PowerDeliveryRes (6-arg EDh)."""
    cmd = "EDh_0_1_1_1_9_2"
    hex_out = exiEncode(cmd)
    decoded = exiDecode(hex_out, "DD")
    parsed = json.loads(decoded)
    assert parsed["DC_EVSEStatus.NotificationMaxDelay"] == "9"
    assert parsed["DC_EVSEStatus.EVSENotification"] == "2"


# =====================================================================
# §9.6  Timing (Tables 76 and 78)
# =====================================================================


def test_timing_constants_match_table_76():
    """Table 76 values are not negotiable — the constants module must
    mirror them exactly."""
    assert V2G_EVCC_MSG_TIMEOUT_DEFAULT_S == 2.0
    assert V2G_EVCC_MSG_TIMEOUT_CURRENT_DEMAND_S == 0.5
    assert V2G_EVCC_SEQUENCE_TIMEOUT_S == 60.0
    assert V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S == 5.0


def test_timing_constants_match_table_78():
    """Table 78 phase-specific wall-clock timers."""
    assert V2G_EVCC_COMMUNICATION_SETUP_TIMEOUT_S == 20.0
    assert V2G_EVCC_READY_TO_CHARGE_TIMEOUT_S == 10.0
    assert V2G_EVCC_CABLE_CHECK_TIMEOUT_S == 38.0
    assert V2G_EVCC_PRE_CHARGE_TIMEOUT_S == 7.0


def test_seconds_to_cycles_at_30ms():
    """The FSM runs at 30 ms/cycle; 2 s → ~66 cycles."""
    assert seconds_to_cycles(2.0) == 67          # ceil of 66.66
    assert seconds_to_cycles(5.0) == 167
    assert seconds_to_cycles(0.5) == 17


def test_pev_precharge_timeout_honours_table_78():
    """The PEV's PreCharge state must abort after the Table 78 PreCharge
    timeout (7 s), not the old 30 s blanket value."""
    from hotwire.fsm import fsm_pev
    f = fsm_pev.fsmPev.__new__(fsm_pev.fsmPev)
    f.state = fsm_pev.STATE_WAIT_PRECHARGE_RES
    f.cyclesInState = seconds_to_cycles(V2G_EVCC_PRE_CHARGE_TIMEOUT_S) - 1
    assert not f.isTooLong(), (
        "PreCharge should not time out before the 7 s Table 78 limit"
    )
    f.cyclesInState = seconds_to_cycles(V2G_EVCC_PRE_CHARGE_TIMEOUT_S) + 5
    assert f.isTooLong(), (
        "PreCharge should time out after the 7 s Table 78 limit"
    )


def test_pev_current_demand_timeout_honours_sequence_5s():
    """V2G_EVCC_SequenceTimeout for CurrentDemand is 5 s, not the old
    0.5 s misreading of the Msg_Timeout column."""
    from hotwire.fsm import fsm_pev
    f = fsm_pev.fsmPev.__new__(fsm_pev.fsmPev)
    f.state = fsm_pev.STATE_WAIT_CURRENT_DEMAND_RES
    f.cyclesInState = seconds_to_cycles(
        V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S
    ) - 1
    assert not f.isTooLong()
    f.cyclesInState = seconds_to_cycles(
        V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S
    ) + 5
    assert f.isTooLong()


def test_pev_session_setup_uses_default_2s_msg_timeout():
    """Generic Req-Res pairs get V2G_EVCC_Msg_Timeout = 2 s."""
    from hotwire.fsm import fsm_pev
    f = fsm_pev.fsmPev.__new__(fsm_pev.fsmPev)
    f.state = fsm_pev.STATE_WAIT_SESSION_SETUP_RES
    f.cyclesInState = seconds_to_cycles(
        V2G_EVCC_MSG_TIMEOUT_DEFAULT_S
    ) - 1
    assert not f.isTooLong()
    f.cyclesInState = seconds_to_cycles(
        V2G_EVCC_MSG_TIMEOUT_DEFAULT_S
    ) + 2
    assert f.isTooLong()


# =====================================================================
# §9.1  ResponseCode enum integrity
# =====================================================================


def test_din_response_codes_include_all_23_table_79_entries():
    """Table 79: the DIN schema's dinresponseCodeType has exactly 23
    enumeration values, numbered 0-22."""
    from hotwire.gui.stage_schema import _DIN_RC, DIN_RC_TO_INT
    assert len(_DIN_RC) == 23
    assert len(DIN_RC_TO_INT) == 23
    assert set(DIN_RC_TO_INT.values()) == set(range(23))


@pytest.mark.parametrize("name", [
    "OK",
    "OK_NewSessionEstablished",
    "OK_OldSessionJoined",
    "OK_CertificateExpiresSoon",
    "FAILED",
    "FAILED_SequenceError",
    "FAILED_EVSEPresentVoltageToLow",   # note: "To" not "Too" — canonical
    "FAILED_WrongChargeParameter",
    "FAILED_WrongEnergyTransferType",
])
def test_key_response_code_names_spelled_exactly(name):
    """Spelling matters — any wire implementation that ships a typo here
    will silently misbehave with a strict peer."""
    from hotwire.gui.stage_schema import DIN_RC_TO_INT
    assert name in DIN_RC_TO_INT


if __name__ == "__main__":
    # Plain-Python runner that expands @pytest.mark.parametrize.
    import traceback
    tests = [(k, v) for k, v in list(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for name, t in tests:
        marks = getattr(t, "pytestmark", [])
        parametrize_args: list[tuple] = []
        for mark in marks:
            if getattr(mark, "name", None) == "parametrize":
                argnames_raw, argvals = mark.args[0], mark.args[1]
                for vals in argvals:
                    if not isinstance(vals, (list, tuple)):
                        vals = (vals,)
                    parametrize_args.append(tuple(vals))
                break
        if parametrize_args:
            for case in parametrize_args:
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
