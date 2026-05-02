"""
Stage schema — describes every configurable DIN 70121 message the GUI can
modify on either side (EVSE Res or PEV Req).

The stage names MUST match the strings passed as the first argument to
``PauseController.intercept()`` in ``fsm_evse.py`` and ``fsm_pev.py``.
The param keys MUST match the keys in those FSMs' ``default_params`` dicts.

Adding a new stage or field here automatically surfaces it in the GUI
config panel and pause-intercept dialog — no widget code changes needed.

Each schema mirrors the arg list the bundled OpenV2G codec accepts for
the corresponding ``E{schemaPrefix}{msgChar}`` command, so every field
reaches the wire. Discovered via ``vendor/probe_openv2g.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..core.modes import C_EVSE_MODE, C_PEV_MODE


@dataclass(frozen=True)
class FieldSpec:
    """Describes one editable parameter within a stage.

    The widget captures a *display* value (e.g. the combo-box text
    ``"OK_NewSessionEstablished"``), while the FSM's EXI command builder
    needs a *wire* value (e.g. the int ``1``). The optional ``to_wire``
    callable performs that conversion right after the widget read and
    before the override is handed to the PauseController. If ``None``,
    the widget value is passed through unchanged.
    """

    key: str                      # dict key seen by FSM / PauseController
    label: str                    # UI label
    widget: str                   # "combo" | "hex" | "int" | "str" | "bool"
    options: tuple[str, ...] = ()  # combo options (display strings → first is default)
    default: Any = None
    tooltip: str = ""
    to_wire: Optional[Callable[[Any], Any]] = None


# --- Enum option lists (mirror hotwire/fsm/constants.py, keep UI-facing) --

_APP_HAND_RC = ("OK_SuccessfulNegotiation",
                "OK_SuccessfulNegotiationWithMinorDeviation",
                "Failed_NoNegotiation")

_DIN_RC = ("OK", "OK_NewSessionEstablished", "OK_OldSessionJoined",
           "OK_CertificateExpiresSoon", "FAILED", "FAILED_SequenceError",
           "FAILED_ServiceIDInvalid", "FAILED_UnknownSession",
           "FAILED_ServiceSelectionInvalid", "FAILED_PaymentSelectionInvalid",
           "FAILED_CertificateExpired", "FAILED_SignatureError",
           "FAILED_NoCertificateAvailable", "FAILED_CertChainError",
           "FAILED_ChallengeInvalid", "FAILED_ContractCanceled",
           "FAILED_WrongChargeParameter", "FAILED_PowerDeliveryNotApplied",
           "FAILED_TariffSelectionInvalid", "FAILED_ChargingProfileInvalid",
           "FAILED_EVSEPresentVoltageToLow", "FAILED_MeteringSignatureNotValid",
           "FAILED_WrongEnergyTransferType")

_EVSE_PROCESSING = ("Finished", "Ongoing")
_ISOLATION = ("Invalid", "Valid", "Warning", "Fault")
_EVSE_STATUS = ("EVSE_NotReady", "EVSE_Ready", "EVSE_Shutdown",
                "EVSE_UtilityInterruptEvent", "EVSE_IsolationMonitoringActive",
                "EVSE_EmergencyShutdown", "EVSE_Malfunction")
_EVSE_NOTIFICATION = ("None", "StopCharging", "ReNegotiation")
# OpenV2G unit symbol enum (ISO 15118 / DIN uses same codes).
_UNIT_SYMBOL = ("h", "m", "s", "A", "Ah", "V", "VA", "W", "W_s", "Wh")
_BOOL_01 = ("1", "0")

# OpenV2G param-number mappings (for enums that are sent as ints).
APP_HAND_RC_TO_INT: dict[str, int] = {s: i for i, s in enumerate(_APP_HAND_RC)}
DIN_RC_TO_INT: dict[str, int] = {
    "OK": 0, "OK_NewSessionEstablished": 1, "OK_OldSessionJoined": 2,
    "OK_CertificateExpiresSoon": 3, "FAILED": 4, "FAILED_SequenceError": 5,
    "FAILED_ServiceIDInvalid": 6, "FAILED_UnknownSession": 7,
    "FAILED_ServiceSelectionInvalid": 8, "FAILED_PaymentSelectionInvalid": 9,
    "FAILED_CertificateExpired": 10, "FAILED_SignatureError": 11,
    "FAILED_NoCertificateAvailable": 12, "FAILED_CertChainError": 13,
    "FAILED_ChallengeInvalid": 14, "FAILED_ContractCanceled": 15,
    "FAILED_WrongChargeParameter": 16, "FAILED_PowerDeliveryNotApplied": 17,
    "FAILED_TariffSelectionInvalid": 18, "FAILED_ChargingProfileInvalid": 19,
    "FAILED_EVSEPresentVoltageToLow": 20, "FAILED_MeteringSignatureNotValid": 21,
    "FAILED_WrongEnergyTransferType": 22,
}
# OpenV2G EDl treats 1=Finished, 0=Ongoing (reversed from intuition — see fsm_evse.py).
EVSE_PROCESSING_EDL_TO_INT: dict[str, int] = {"Finished": 1, "Ongoing": 0}
# EDe / EDf treat 0=Finished, 1=Ongoing (normal).
EVSE_PROCESSING_TO_INT: dict[str, int] = {"Finished": 0, "Ongoing": 1}
ISOLATION_TO_INT: dict[str, int] = {s: i for i, s in enumerate(_ISOLATION)}
EVSE_STATUS_TO_INT: dict[str, int] = {s: i for i, s in enumerate(_EVSE_STATUS)}
EVSE_NOTIFICATION_TO_INT: dict[str, int] = {s: i for i, s in enumerate(_EVSE_NOTIFICATION)}
UNIT_SYMBOL_TO_INT: dict[str, int] = {s: i for i, s in enumerate(_UNIT_SYMBOL)}


# --- to_wire converters -----------------------------------------------------


def _to_app_hand_rc(s: Any) -> int:
    return APP_HAND_RC_TO_INT.get(str(s), 0)


def _to_din_rc(s: Any) -> int:
    return DIN_RC_TO_INT.get(str(s), 0)


def _to_evse_proc_edl(s: Any) -> int:
    return EVSE_PROCESSING_EDL_TO_INT.get(str(s), 1)


def _to_evse_proc(s: Any) -> int:
    return EVSE_PROCESSING_TO_INT.get(str(s), 0)


def _to_isolation(s: Any) -> int:
    return ISOLATION_TO_INT.get(str(s), 0)


def _to_evse_status(s: Any) -> int:
    return EVSE_STATUS_TO_INT.get(str(s), 1)


def _to_evse_notification(s: Any) -> int:
    return EVSE_NOTIFICATION_TO_INT.get(str(s), 0)


def _to_unit(s: Any) -> int:
    return UNIT_SYMBOL_TO_INT.get(str(s), 0)


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# --- Field-set fragments (reusable across stages) -----------------------


def _rc_field(default: str = "OK") -> FieldSpec:
    return FieldSpec("ResponseCode", "ResponseCode", "combo",
                     options=_DIN_RC, default=default, to_wire=_to_din_rc)


def _dc_evse_status_fields() -> tuple[FieldSpec, ...]:
    """The five members of DC_EVSEStatusType (§9.5.3 Table 66)."""
    return (
        FieldSpec("IsolationStatusUsed", "Isolation Status used?",
                  "combo", options=_BOOL_01, default="1", to_wire=_to_int,
                  tooltip="Whether the isolation status field is present."),
        FieldSpec("IsolationStatus", "Isolation Status", "combo",
                  options=_ISOLATION, default="Valid", to_wire=_to_isolation),
        FieldSpec("EVSEStatusCode", "EVSE Status Code", "combo",
                  options=_EVSE_STATUS, default="EVSE_Ready",
                  to_wire=_to_evse_status),
        FieldSpec("NotificationMaxDelay", "Notification max delay (s)",
                  "int", default=0,
                  tooltip="EV should honour notification within this many seconds."),
        FieldSpec("EVSENotification", "EVSE Notification", "combo",
                  options=_EVSE_NOTIFICATION, default="None",
                  to_wire=_to_evse_notification),
    )


def _physval_fields(key_prefix: str, label_prefix: str,
                    default_value: int, default_unit: str = "V") -> tuple[FieldSpec, ...]:
    """Multiplier/Value/Unit triplet used throughout DC_ types."""
    return (
        FieldSpec(f"{key_prefix}Multiplier", f"{label_prefix} multiplier (×10^n)",
                  "int", default=0, to_wire=_to_int),
        FieldSpec(key_prefix, f"{label_prefix} value", "int",
                  default=default_value, to_wire=_to_int),
        FieldSpec(f"{key_prefix}Unit", f"{label_prefix} unit", "combo",
                  options=_UNIT_SYMBOL, default=default_unit, to_wire=_to_unit),
    )


def _max_limit_fields(key_prefix: str, label_prefix: str,
                      default_value: int, default_unit: str,
                      default_multiplier: int = 0,
                      default_used: str = "1") -> tuple[FieldSpec, ...]:
    """Optional EVSEMaximum*Limit — used flag + physval triplet."""
    return (
        FieldSpec(f"{key_prefix}_isUsed", f"{label_prefix} used?", "combo",
                  options=_BOOL_01, default=default_used, to_wire=_to_int),
        FieldSpec(f"{key_prefix}Multiplier", f"{label_prefix} multiplier (×10^n)",
                  "int", default=default_multiplier, to_wire=_to_int),
        FieldSpec(key_prefix, f"{label_prefix} value", "int",
                  default=default_value, to_wire=_to_int),
        FieldSpec(f"{key_prefix}Unit", f"{label_prefix} unit", "combo",
                  options=_UNIT_SYMBOL, default=default_unit, to_wire=_to_unit),
    )


# --- EVSE-side schemas (Res messages) -----------------------------------

STAGE_SCHEMAS_EVSE: dict[str, tuple[FieldSpec, ...]] = {
    # Eh: 3 args (rc, SchemaID_isUsed, SchemaID)
    "supportedAppProtocolRes": (
        FieldSpec("ResponseCode", "ResponseCode (enum)", "combo",
                  options=_APP_HAND_RC, default="OK_SuccessfulNegotiation",
                  tooltip="App-handshake outcome", to_wire=_to_app_hand_rc),
        FieldSpec("SchemaID_isUsed", "SchemaID used?", "combo",
                  options=_BOOL_01, default="1",
                  tooltip="Whether the response carries a SchemaID",
                  to_wire=_to_int),
        FieldSpec("SchemaID", "SchemaID", "int", default=1,
                  tooltip="Offered schema ID echoed back (normally 1 for DIN)"),
    ),

    # EDa: 2 args (rc, EVSEID_hex)
    "SessionSetupRes": (
        _rc_field("OK_NewSessionEstablished"),
        FieldSpec("EVSEID", "EVSEID (hex)", "hex", default="5A5A4445464C54",
                  tooltip="7-byte EVSE identifier, hex-encoded. Default = 'ZZDEFLT'."),
    ),

    # EDb: 11 args. The shipped OpenV2G binary's custom-params branch
    # activates only when >= 11 positional args are supplied (probed
    # empirically against the vendored codec). Layout matches the builder
    # in hotwire.fsm.fsm_evse._state_wait_service_discovery().
    "ServiceDiscoveryRes": (
        _rc_field("OK"),
        FieldSpec(
            key="PaymentOption", label="Payment option",
            widget="combo",
            options=(("Contract", 0), ("ExternalPayment", 1)),
            default="ExternalPayment",
            tooltip="Method the EVSE advertises; DIN Table 94.",
        ),
        FieldSpec(
            key="ServiceID", label="Service ID", widget="int",
            default=1,
            tooltip="Arbitrary ID for the charging service (default 1).",
        ),
        FieldSpec(
            key="ServiceCategory", label="Service category",
            widget="combo",
            options=(
                ("EVCharging", 0), ("Internet", 1),
                ("ContractCertificate", 2), ("OtherCustom", 3),
            ),
            default="EVCharging",
            tooltip="ChargeService.ServiceCategory.",
        ),
        FieldSpec(
            key="FreeService", label="Free service?", widget="combo",
            options=(("No (paid)", 0), ("Yes (free)", 1)),
            default="No (paid)",
        ),
        FieldSpec(
            key="EnergyTransferType", label="Energy transfer type",
            widget="combo",
            options=(
                ("AC_single_phase_core", 0),
                ("AC_three_phase_core", 1),
                ("DC_core", 2),
                ("DC_extended", 3),
                ("DC_combo_core", 4),
                ("DC_dual", 5),
            ),
            default="DC_extended",
            tooltip="The CCS variant the EVSE supports.",
        ),
        # Reserved extension slots 6..10. Default 0; operators may probe
        # them during fuzzing. The binary ignores out-of-band values
        # cleanly, so no clamp is applied.
        FieldSpec(key="sd_reserved_6", label="reserved[6]", widget="int", default=0),
        FieldSpec(key="sd_reserved_7", label="reserved[7]", widget="int", default=0),
        FieldSpec(key="sd_reserved_8", label="reserved[8]", widget="int", default=0),
        FieldSpec(key="sd_reserved_9", label="reserved[9]", widget="int", default=0),
        FieldSpec(key="sd_reserved_10", label="reserved[10]", widget="int", default=0),
    ),

    # EDc: 1 arg (rc)
    "ServicePaymentSelectionRes": (
        _rc_field("OK"),
    ),

    # EDl: 2 args (proc, rc). Proc is REVERSED: 1 = Finished, 0 = Ongoing.
    "ContractAuthenticationRes": (
        FieldSpec("EVSEProcessing", "EVSEProcessing", "combo",
                  options=_EVSE_PROCESSING, default="Finished",
                  tooltip="'Ongoing' tells the EV to re-request.",
                  to_wire=_to_evse_proc_edl),
        _rc_field("OK"),
    ),

    # EDe: 27 args. ChargeParameterDiscoveryRes is the beefiest response.
    # Order (from archive/legacy-evse/fsmEvse.py:659-672):
    #   rc, proc, saslUsed, saslLen, schedStart, pMax,
    #   isoUsed, isoStatus, statusCode, notifDelay, notifType,
    #   maxI_mult, maxI_val, maxI_unit,
    #   maxP_used, maxP_mult, maxP_val, maxP_unit,
    #   maxV_mult, maxV_val, maxV_unit,
    #   minI_mult, minI_val, minI_unit,
    #   minV_mult, minV_val, minV_unit.
    "ChargeParameterDiscoveryRes": (
        _rc_field("OK"),
        FieldSpec("EVSEProcessing", "EVSEProcessing", "combo",
                  options=_EVSE_PROCESSING, default="Finished",
                  to_wire=_to_evse_proc),
        # Operator knob: how many rounds of ``EVSEProcessing=Ongoing``
        # to send before flipping to ``Finished``. Real chargers do
        # this while running internal startup; HotWire defaults to 0
        # (= immediately Finished) for testbed throughput, but bench
        # operators raise it to e.g. 3 to mimic a slow real EVSE.
        FieldSpec("EVSEProcessing_Ongoing_Count", "Ongoing rounds before Finished",
                  "int", default=0, to_wire=_to_int,
                  tooltip="0 = reply Finished immediately. N = reply "
                          "Ongoing N times then Finished. Mirrors real "
                          "EVSE startup latency."),
        FieldSpec("SAScheduleList_isUsed", "SAScheduleList present?", "combo",
                  options=_BOOL_01, default="1", to_wire=_to_int),
        FieldSpec("SAScheduleListArrayLen", "SASchedule tuple array length",
                  "int", default=1, to_wire=_to_int),
        FieldSpec("SchedTupleStart", "PMax schedule start (s)",
                  "int", default=0, to_wire=_to_int),
        FieldSpec("PMax", "PMax (W schedule max power)", "int",
                  default=50, to_wire=_to_int),
        FieldSpec("IsolationStatusUsed", "Isolation Status used?", "combo",
                  options=_BOOL_01, default="1", to_wire=_to_int),
        FieldSpec("IsolationStatus", "Isolation Status", "combo",
                  options=_ISOLATION, default="Invalid", to_wire=_to_isolation),
        FieldSpec("EVSEStatusCode", "EVSE Status Code", "combo",
                  options=_EVSE_STATUS, default="EVSE_Ready",
                  to_wire=_to_evse_status),
        FieldSpec("NotificationMaxDelay", "Notification max delay (s)",
                  "int", default=0, to_wire=_to_int),
        FieldSpec("EVSENotification", "EVSE Notification", "combo",
                  options=_EVSE_NOTIFICATION, default="None",
                  to_wire=_to_evse_notification),
        # Max current limit (MANDATORY in DIN).
        *_physval_fields("EVSEMaximumCurrentLimit", "Max current limit", 200, "A"),
        # Max power limit (optional).
        FieldSpec("EVSEMaximumPowerLimit_isUsed", "Max power limit used?",
                  "combo", options=_BOOL_01, default="1", to_wire=_to_int),
        FieldSpec("EVSEMaximumPowerLimitMultiplier",
                  "Max power limit multiplier (×10^n)", "int",
                  default=3, to_wire=_to_int),
        FieldSpec("EVSEMaximumPowerLimit", "Max power limit value (W)",
                  "int", default=90, to_wire=_to_int,
                  tooltip="With multiplier=3 → 90 × 10^3 W = 90 kW. "
                          "Matches the V×I implied by Max Voltage 450 V "
                          "× Max Current 200 A. Earlier default was 10 "
                          "(= 10 kW) which throttled some BMSes that use "
                          "PowerLimit for their charge profile."),
        FieldSpec("EVSEMaximumPowerLimitUnit", "Max power unit", "combo",
                  options=_UNIT_SYMBOL, default="W", to_wire=_to_unit),
        # Max voltage limit (MANDATORY in DIN).
        *_physval_fields("EVSEMaximumVoltageLimit", "Max voltage limit", 450, "V"),
        # Min current / voltage limits (mandatory).
        *_physval_fields("EVSEMinimumCurrentLimit", "Min current limit", 1, "A"),
        *_physval_fields("EVSEMinimumVoltageLimit", "Min voltage limit", 200, "V"),
    ),

    # EDf: 6 args (proc, statusCode, isoStatus, isoUsed, notifDelay, notifType).
    # Checkpoint 7 introduced the 6-arg form — keep schema in sync.
    "CableCheckRes": (
        FieldSpec("EVSEProcessing", "EVSEProcessing", "combo",
                  options=_EVSE_PROCESSING, default="Finished",
                  to_wire=_to_evse_proc),
        FieldSpec("EVSEProcessing_Ongoing_Count", "Ongoing rounds before Finished",
                  "int", default=0, to_wire=_to_int,
                  tooltip="Reply with EVSEProcessing=Ongoing this many "
                          "times before flipping to Finished. Real "
                          "chargers ramp through ~5 s of isolation "
                          "checking; raise this to mimic that latency."),
        FieldSpec("EVSEStatusCode", "EVSE Status Code", "combo",
                  options=_EVSE_STATUS, default="EVSE_Ready",
                  to_wire=_to_evse_status),
        FieldSpec("IsolationStatus", "Isolation Status", "combo",
                  options=_ISOLATION, default="Valid", to_wire=_to_isolation),
        FieldSpec("IsolationStatusUsed", "Isolation Used?",
                  "combo", options=_BOOL_01, default="1", to_wire=_to_int),
        FieldSpec("NotificationMaxDelay", "Notification max delay (s)",
                  "int", default=0, to_wire=_to_int),
        FieldSpec("EVSENotification", "EVSE Notification", "combo",
                  options=_EVSE_NOTIFICATION, default="None",
                  to_wire=_to_evse_notification),
    ),

    # EDg: 7 args (EVSEPresentVoltage, rc, isoUsed, isoStatus, statusCode,
    # notifDelay, notifType). Note: EVSEPresentVoltage comes FIRST.
    "PreChargeRes": (
        FieldSpec("EVSEPresentVoltage", "EVSE Present Voltage (V)", "int",
                  default=350,
                  tooltip="Voltage reported as present at EVSE output. Must be "
                          "close to EVTargetVoltage for PEV to exit precharge."),
        _rc_field("OK"),
        FieldSpec("IsolationStatusUsed", "Isolation Status used?",
                  "combo", options=_BOOL_01, default="1", to_wire=_to_int),
        FieldSpec("IsolationStatus", "Isolation Status", "combo",
                  options=_ISOLATION, default="Valid", to_wire=_to_isolation),
        FieldSpec("EVSEStatusCode", "EVSE Status Code", "combo",
                  options=_EVSE_STATUS, default="EVSE_Ready",
                  to_wire=_to_evse_status),
        FieldSpec("NotificationMaxDelay", "Notification max delay (s)",
                  "int", default=0, to_wire=_to_int),
        FieldSpec("EVSENotification", "EVSE Notification", "combo",
                  options=_EVSE_NOTIFICATION, default="None",
                  to_wire=_to_evse_notification),
    ),

    # EDh: 6 args (rc, isoUsed, isoStatus, statusCode, notifDelay, notifType).
    "PowerDeliveryRes": (
        _rc_field("OK"),
        *_dc_evse_status_fields(),
    ),

    # EDi: 27 args. CurrentDemandRes is the most complex response.
    "CurrentDemandRes": (
        _rc_field("OK"),
        *_dc_evse_status_fields(),
        *_physval_fields("EVSEPresentVoltage", "Present voltage", 400, "V"),
        *_physval_fields("EVSEPresentCurrent", "Present current", 50, "A"),
        FieldSpec("EVSECurrentLimitAchieved", "Current limit achieved?",
                  "combo", options=_BOOL_01, default="0", to_wire=_to_int),
        FieldSpec("EVSEVoltageLimitAchieved", "Voltage limit achieved?",
                  "combo", options=_BOOL_01, default="0", to_wire=_to_int),
        FieldSpec("EVSEPowerLimitAchieved", "Power limit achieved?",
                  "combo", options=_BOOL_01, default="0", to_wire=_to_int),
        # DIN mandates all three max-limits (V2G-DC-948/949/950).
        *_max_limit_fields("EVSEMaximumVoltageLimit",
                           "Max voltage limit", 450, "V",
                           default_used="1"),
        *_max_limit_fields("EVSEMaximumCurrentLimit",
                           "Max current limit", 200, "A",
                           default_used="1"),
        *_max_limit_fields("EVSEMaximumPowerLimit",
                           "Max power limit", 60, "W",
                           default_multiplier=3, default_used="1"),
    ),

    # EDj: 9 args (rc, isoUsed, isoStatus, statusCode, notifDelay, notifType,
    # presentV_mult, presentV_val, presentV_unit).
    "WeldingDetectionRes": (
        _rc_field("OK"),
        *_dc_evse_status_fields(),
        *_physval_fields("EVSEPresentVoltage", "Present voltage", 0, "V"),
    ),

    # EDk: 1 arg (rc). SessionStopRes is minimal.
    "SessionStopRes": (
        _rc_field("OK"),
    ),
}


# --- PEV-side schemas (Req messages) -------------------------------------

STAGE_SCHEMAS_PEV: dict[str, tuple[FieldSpec, ...]] = {
    "SessionSetupReq": (
        FieldSpec("EVCCID", "EVCCID (hex)", "hex", default="",
                  tooltip="EV's 6-byte MAC identifier (impersonation target). "
                          "Leave blank to use the machine's local MAC."),
    ),
    "ServiceDiscoveryReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
    ),
    "ServicePaymentSelectionReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
    ),
    "ContractAuthenticationReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
    ),
    "ChargeParameterDiscoveryReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
        FieldSpec("SoC", "State of Charge (%)", "int", default=30,
                  tooltip="Battery SoC reported to the EVSE. Used for billing / display."),
    ),
    "CableCheckReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
        FieldSpec("SoC", "State of Charge (%)", "int", default=30),
    ),
    "PreChargeReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
        FieldSpec("SoC", "State of Charge (%)", "int", default=30),
        FieldSpec("EVTargetVoltage", "EV Target Voltage (V)", "int", default=350,
                  tooltip="Requested pre-charge voltage."),
        FieldSpec("EVTargetCurrent", "EV Target Current (A)", "int", default=1,
                  tooltip="EDG codec ignores this — placeholder for when OpenV2G "
                          "is patched to accept a 4th positional arg."),
    ),
    "PowerDeliveryReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
        FieldSpec("SoC", "State of Charge (%)", "int", default=30),
        FieldSpec("ReadyToChargeState", "Ready To Charge", "combo",
                  options=_BOOL_01, default="1",
                  tooltip="1 = start charging, 0 = stop charging."),
    ),
    "CurrentDemandReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
        FieldSpec("SoC", "State of Charge (%)", "int", default=30),
        FieldSpec("EVTargetCurrent", "EV Target Current (A)", "int", default=125),
        FieldSpec("EVTargetVoltage", "EV Target Voltage (V)", "int", default=400),
    ),
    "WeldingDetectionReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
        FieldSpec("SoC", "State of Charge (%)", "int", default=30),
    ),
    "SessionStopReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
    ),
    # Even though OpenV2G can't synthesise an ISO-capable
    # supportedAppProtocolReq from command-line args, the PEV FSM now
    # routes the pre-captured blob through PauseController so the GUI /
    # a playbook can (a) observe it in the session log and (b) swap in
    # an alternative blob via the PayloadHex override key.
    "supportedAppProtocolReq": (
        FieldSpec("PayloadHex", "Raw EXI payload (hex)", "str", default="",
                  tooltip="Hex-encoded supportedAppProtocolReq. If empty the "
                          "FSM picks the blob named by 'Preset'."),
        FieldSpec("Preset", "Pre-captured blob preset", "combo",
                  options=("ioniq", "tesla", "generated"),
                  default="ioniq",
                  tooltip="'ioniq' = Hyundai Ioniq DIN-only blob; "
                          "'tesla' = Tesla Model Y DIN+proprietary; "
                          "'generated' = runtime EH_ build (requires a codec "
                          "upgrade, falls back to 'ioniq' when unavailable)."),
    ),
}


def schema_for(mode: int) -> dict[str, tuple[FieldSpec, ...]]:
    """Return the stage schema dict for the given HotWire mode."""
    if mode == C_EVSE_MODE:
        return STAGE_SCHEMAS_EVSE
    if mode == C_PEV_MODE:
        return STAGE_SCHEMAS_PEV
    return {}


def stage_order(mode: int) -> list[str]:
    """Canonical display order (handshake → charging → stop)."""
    if mode == C_EVSE_MODE:
        return [
            "supportedAppProtocolRes", "SessionSetupRes", "ServiceDiscoveryRes",
            "ServicePaymentSelectionRes", "ContractAuthenticationRes",
            "ChargeParameterDiscoveryRes", "CableCheckRes", "PreChargeRes",
            "PowerDeliveryRes", "CurrentDemandRes", "WeldingDetectionRes",
            "SessionStopRes",
        ]
    if mode == C_PEV_MODE:
        return [
            "supportedAppProtocolReq",
            "SessionSetupReq", "ServiceDiscoveryReq", "ServicePaymentSelectionReq",
            "ContractAuthenticationReq", "ChargeParameterDiscoveryReq",
            "CableCheckReq", "PreChargeReq", "PowerDeliveryReq",
            "CurrentDemandReq", "WeldingDetectionReq", "SessionStopReq",
        ]
    return []
