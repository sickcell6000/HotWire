"""Shared FSM constants — DIN 70121 enum tables mirroring OpenV2G's C enums."""

from __future__ import annotations

# supportedAppProtocolRes response codes.
APP_HAND_RES_OK = 0
APP_HAND_RES_OK_MINOR_DEVIATION = 1
APP_HAND_RES_FAILED_NO_NEGOTIATION = 2

# dinresponseCodeType.
DIN_RC_OK = 0
DIN_RC_OK_NEW_SESSION = 1
DIN_RC_OK_OLD_SESSION = 2
DIN_RC_FAILED = 4
DIN_RC_FAILED_SEQUENCE_ERROR = 5

# dinEVSEProcessingType.
DIN_PROC_FINISHED = "0"
DIN_PROC_ONGOING = "1"

# dinisolationLevelType.
ISOL_INVALID = 0
ISOL_VALID = 1
ISOL_WARNING = 2
ISOL_FAULT = 3

# dinDC_EVSEStatusCodeType.
EVSE_NOT_READY = 0
EVSE_READY = 1
EVSE_SHUTDOWN = 2
EVSE_UTILITY_INTERRUPT = 3
EVSE_ISOLATION_MONITOR_ACTIVE = 4
EVSE_EMERGENCY_SHUTDOWN = 5
EVSE_MALFUNCTION = 6

# dinEVSENotificationType.
EVSE_NOTIF_NONE = 0
EVSE_NOTIF_STOP_CHARGING = 1
EVSE_NOTIF_RENEGOTIATION = 2

# Default EVSE identity (7-byte ASCII 'ZZDEFLT' as hex).
DEFAULT_EVSE_ID_HEX = "5A5A4445464C54"

# Default session ID before SessionSetup (also used by PEV prior to receiving real one).
DEFAULT_SESSION_ID_HEX = "DEAD55AADEAD55AA"

# Callback interval assumed by every FSM cycle (30 ms).
FSM_CYCLE_MS = 30
FSM_CYCLES_PER_SECOND = 33


def is_error_evse_status_code(code_str: str) -> bool:
    """Return True if the EVSE reported a fatal status code we must abort on."""
    try:
        code = int(code_str)
    except (TypeError, ValueError):
        return False
    return code in (
        EVSE_SHUTDOWN,
        EVSE_UTILITY_INTERRUPT,
        EVSE_EMERGENCY_SHUTDOWN,
        EVSE_MALFUNCTION,
        7, 8, 9, 10, 11,
    )
