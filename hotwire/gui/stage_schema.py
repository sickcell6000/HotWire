"""
Stage schema — describes every configurable DIN 70121 message the GUI can
modify on either side (EVSE Res or PEV Req).

The stage names MUST match the strings passed as the first argument to
``PauseController.intercept()`` in ``fsm_evse.py`` and ``fsm_pev.py``.
The param keys MUST match the keys in those FSMs' ``default_params`` dicts.

Adding a new stage or field here automatically surfaces it in the GUI
config panel and pause-intercept dialog — no widget code changes needed.
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
# EDf treats 0=Finished, 1=Ongoing (normal).
EVSE_PROCESSING_TO_INT: dict[str, int] = {"Finished": 0, "Ongoing": 1}
ISOLATION_TO_INT: dict[str, int] = {s: i for i, s in enumerate(_ISOLATION)}
EVSE_STATUS_TO_INT: dict[str, int] = {s: i for i, s in enumerate(_EVSE_STATUS)}


# --- EVSE-side schemas (Res messages) ------------------------------------


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


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


STAGE_SCHEMAS_EVSE: dict[str, tuple[FieldSpec, ...]] = {
    "supportedAppProtocolRes": (
        FieldSpec("ResponseCode", "ResponseCode (enum)", "combo",
                  options=_APP_HAND_RC, default="OK_SuccessfulNegotiation",
                  tooltip="App-handshake outcome", to_wire=_to_app_hand_rc),
        FieldSpec("SchemaID_isUsed", "SchemaID used?", "combo",
                  options=("1", "0"), default="1",
                  tooltip="Whether the response carries a SchemaID",
                  to_wire=_to_int),
        FieldSpec("SchemaID", "SchemaID", "int", default=1,
                  tooltip="Offered schema ID echoed back (normally 1 for DIN)"),
    ),
    "SessionSetupRes": (
        FieldSpec("ResponseCode", "ResponseCode", "combo",
                  options=_DIN_RC, default="OK_NewSessionEstablished",
                  to_wire=_to_din_rc),
        FieldSpec("EVSEID", "EVSEID (hex)", "hex", default="5A5A4445464C54",
                  tooltip="7-byte EVSE identifier, hex-encoded. Default = 'ZZDEFLT'."),
    ),
    "ServiceDiscoveryRes": (),
    "ServicePaymentSelectionRes": (),
    "ContractAuthenticationRes": (
        # Note: the EDl command uses the REVERSED encoding (1=Finished, 0=Ongoing).
        FieldSpec("EVSEProcessing", "EVSEProcessing", "combo",
                  options=("Finished", "Ongoing"), default="Finished",
                  tooltip="'Ongoing' tells the EV to re-request.",
                  to_wire=_to_evse_proc_edl),
        FieldSpec("ResponseCode", "ResponseCode", "combo",
                  options=_DIN_RC, default="OK", to_wire=_to_din_rc),
    ),
    "ChargeParameterDiscoveryRes": (),
    "CableCheckRes": (
        FieldSpec("EVSEProcessing", "EVSEProcessing", "combo",
                  options=_EVSE_PROCESSING, default="Finished",
                  to_wire=_to_evse_proc),
        FieldSpec("EVSEStatusCode", "EVSE Status Code", "combo",
                  options=_EVSE_STATUS, default="EVSE_Ready",
                  to_wire=_to_evse_status),
        FieldSpec("IsolationStatus", "Isolation Status", "combo",
                  options=_ISOLATION, default="Valid",
                  to_wire=_to_isolation),
        FieldSpec("IsolationStatusUsed", "Isolation Used?", "combo",
                  options=("1", "0"), default="1", to_wire=_to_int),
    ),
    "PreChargeRes": (
        FieldSpec("EVSEPresentVoltage", "EVSE Present Voltage (V)", "int",
                  default=350,
                  tooltip="Voltage reported as present at EVSE output. "
                          "Must be close to EVTargetVoltage for PEV to exit precharge."),
    ),
    "PowerDeliveryRes": (),
    "CurrentDemandRes": (),
    "WeldingDetectionRes": (),
    "SessionStopRes": (),
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
    ),
    "PowerDeliveryReq": (
        FieldSpec("SessionID", "SessionID (hex)", "hex", default=""),
        FieldSpec("SoC", "State of Charge (%)", "int", default=30),
        FieldSpec("ReadyToChargeState", "Ready To Charge", "combo",
                  options=("1", "0"), default="1",
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
            "SessionSetupReq", "ServiceDiscoveryReq", "ServicePaymentSelectionReq",
            "ContractAuthenticationReq", "ChargeParameterDiscoveryReq",
            "CableCheckReq", "PreChargeReq", "PowerDeliveryReq",
            "CurrentDemandReq", "WeldingDetectionReq", "SessionStopReq",
        ]
    return []
