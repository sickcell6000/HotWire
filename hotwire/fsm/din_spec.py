"""
DIN TS 70121:2024-11 protocol-level constants.

Anything here is a literal quote from the standard (with the § reference
next to it) — do NOT change values without citing a new revision. The
timing layer in particular is how the FSMs decide when to time out each
request / response, and those timers matter for interop with real EVs.

References throughout are to DIN TS 70121:2024-11 section numbers.
"""
from __future__ import annotations

FSM_CYCLE_MS = 30               # the FSM tick rate (empirical, matches pyPLC)


# --- §8.7.2 Supported ports --------------------------------------------

# The standard does NOT mandate port 15118. It mandates BOTH peers pick
# a port in the IANA dynamic range (49152-65535). The SECC publishes the
# chosen port in its SDP response; the EVCC uses whatever it reads there.
TCP_PORT_MIN = 49152            # IANA dynamic range lower bound
TCP_PORT_MAX = 65535            # upper bound
TCP_PORT_WELL_KNOWN = 15118     # common convention but not a spec requirement


# --- §9.2 supportedAppProtocol Response codes --------------------------

APP_HAND_RC_OK = 0                                  # OK_SuccessfulNegotiation
APP_HAND_RC_OK_MINOR_DEVIATION = 1                  # OK_SuccessfulNegotiationWithMinorDeviation
APP_HAND_RC_FAILED_NO_NEGOTIATION = 2               # Failed_NoNegotiation
# V2G-DC-226: when no common protocol, ResponseCode MUST be
# Failed_NoNegotiation and the response SHALL NOT include a SchemaID.


# --- §9.6 / Table 76 — timing constants --------------------------------

# All in seconds. Converted to cycles at runtime via FSM_CYCLE_MS.
#
# Message-level timeouts (how long to wait for the RESPONSE to a single
# request). Most messages use V2G_EVCC_Msg_Timeout = 2 s.
V2G_EVCC_MSG_TIMEOUT_DEFAULT_S = 2.0
# CurrentDemand has its own short message timeout — the charging loop
# runs fast (4-40 Hz) and a stale Res means power-supply drift.
V2G_EVCC_MSG_TIMEOUT_CURRENT_DEMAND_S = 0.5

# Sequence-level timeouts (how long the whole ongoing "phase" may last).
V2G_EVCC_SEQUENCE_TIMEOUT_S = 60.0                  # most phases
V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S = 5.0    # charging loop

# Ongoing-loop timeout (for phases that re-request while EVSEProcessing
# = "Ongoing", e.g. ContractAuthentication, ChargeParameterDiscovery,
# CableCheck). The EVCC must give up after this total wall time.
V2G_EVCC_ONGOING_TIMEOUT_S = 60.0

# Phase-specific wall-clock timers (Table 78).
V2G_EVCC_COMMUNICATION_SETUP_TIMEOUT_S = 20.0       # TCP connect + appHandshake
V2G_EVCC_READY_TO_CHARGE_TIMEOUT_S = 10.0           # from ChargeParameterDiscovery done to PowerDelivery confirmed
V2G_EVCC_CABLE_CHECK_TIMEOUT_S = 38.0               # total cable check duration
V2G_EVCC_PRE_CHARGE_TIMEOUT_S = 7.0                 # DIN 70121 spec value, retained for conformance tests

# Empirical pyPLC value for the PreCharge phase. The DIN spec says 7 s,
# but real EVSE DC ramp-up regularly takes longer (the original pyPLC
# comment notes 30 s minimum has been observed against commercial
# chargers — Compleo, ABB, etc.). HotWire's runtime ``isTooLong`` uses
# this lenient value so paper-validation runs against real hardware
# don't false-timeout PreCharge while the EVSE is mid-ramp; the
# spec-faithful value above is still exposed for conformance tests in
# tests/test_din_conformance.py.
V2G_EVCC_PRE_CHARGE_TIMEOUT_LENIENT_S = 30.0


# --- §9.4 Message mandatory fields — enforced-by-default flags ---------

# V2G-DC-948, V2G-DC-949, V2G-DC-950: in a DIN 70121 CurrentDemandRes, the
# three max-limit elements are MANDATORY (unlike ISO 15118-2 where they're
# optional). HotWire's command-line codec accepts "_isUsed" flags; we set
# them to 1 by default and use plausible default values so a bare
# CurrentDemandRes still conforms.
CURRENT_DEMAND_RES_MAX_V_DEFAULT = 450              # 450 V
CURRENT_DEMAND_RES_MAX_I_DEFAULT = 200              # 200 A
CURRENT_DEMAND_RES_MAX_P_POWER = 60                 # 60 * 10^3 W = 60 kW
CURRENT_DEMAND_RES_MAX_P_MULTIPLIER = 3             # value × 10^3


# --- Helpers ------------------------------------------------------------

def seconds_to_cycles(seconds: float) -> int:
    """Convert a wall-clock timeout (seconds) to FSM cycles."""
    return max(1, int(round(seconds * 1000 / FSM_CYCLE_MS)))
