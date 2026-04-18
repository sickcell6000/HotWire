"""
DIN 70121 EVSE (charging station) state machine.

This is a modernized port of the legacy ``archive/legacy-evse/fsmEvse.py``
(1440 lines, pyPLC GPL-3.0) cut down to the essentials needed to drive
the charging session end-to-end:

    SupportedAppProtocol  -> negotiated DIN 70121 schema
    SessionSetup          -> new session
    ServiceDiscovery      -> DC charging, ExternalPayment
    ServicePaymentSelection
    (FlexibleRequest loop: ContractAuthentication / ChargeParameterDiscovery /
     CableCheck / PreCharge / PowerDelivery / CurrentDemand / WeldingDetection /
     SessionStop)

All Reqs from the peer are inspected and decoded; default-valid bare-encoded
responses are emitted. Every Res passes through ``PauseController.intercept``
so the GUI layer in Checkpoint 3 can edit parameters before transmission.

Adapted from pyPLC's fsmEvse.py (GPL-3.0, uhi22).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ..exi.connector import (
    addV2GTPHeader,
    exiDecode,
    exiEncode,
    removeV2GTPHeader,
)
from ..helpers import prettyHexMessage
from ..plc.tcp_socket import (
    STATE_CONNECTED,
    STATE_DISCONNECTED,
    STATE_LISTENING,
    pyPlcTcpServerSocket,
)
import json as _json

from .constants import DEFAULT_EVSE_ID_HEX
from .message_observer import MessageObserver
from .pause_controller import PauseController


# --- State numbers ------------------------------------------------------

STATE_WAIT_APP_HANDSHAKE = 0
STATE_WAIT_SESSION_SETUP = 1
STATE_WAIT_SERVICE_DISCOVERY = 2
STATE_WAIT_SERVICE_PAYMENT = 3
STATE_WAIT_FLEXIBLE = 4
STATE_STOPPED = 9


_STATE_NAMES = {
    STATE_WAIT_APP_HANDSHAKE: "WaitForAppHandshake",
    STATE_WAIT_SESSION_SETUP: "WaitForSessionSetup",
    STATE_WAIT_SERVICE_DISCOVERY: "WaitForServiceDiscovery",
    STATE_WAIT_SERVICE_PAYMENT: "WaitForServicePayment",
    STATE_WAIT_FLEXIBLE: "WaitForFlexibleRequest",
    STATE_STOPPED: "Stopped",
}


class fsmEvse:
    """EVSE-side DIN 70121 state machine driving a single TCP session."""

    def __init__(
        self,
        addressManager,
        callbackAddToTrace: Callable[[str], None],
        hardwareInterface,
        callbackShowStatus: Callable[[str, str], None],
        pause_controller: Optional[PauseController] = None,
        message_observer: Optional[MessageObserver] = None,
    ) -> None:
        self.addressManager = addressManager
        self.callbackAddToTrace = callbackAddToTrace
        self.hardwareInterface = hardwareInterface
        self.callbackShowStatus = callbackShowStatus
        self.pause_controller = pause_controller or PauseController()
        self.message_observer = message_observer

        self.state = STATE_WAIT_APP_HANDSHAKE
        self.cyclesInState = 0
        self.rxData: bytes = b""
        self.evccid: str = ""
        self.blChargeStopTrigger = False
        self.schemaSelection = "D"  # "D" for DIN 70121, "1" for ISO 15118-2

        self.addToTrace("Initializing fsmEvse")

        self.Tcp = pyPlcTcpServerSocket(
            self.callbackAddToTrace,
            self._socketStateNotification,
            self.addressManager.getLinkLocalIpv6Address(),
        )

        self.publishStatus("Waiting for AppHandshake")

    # ---- logging / status plumbing ---------------------------------

    def addToTrace(self, s: str) -> None:
        self.callbackAddToTrace("[EVSE] " + s)

    def publishStatus(self, s: str) -> None:
        self.callbackShowStatus(s, "evseState")

    # ---- lifecycle --------------------------------------------------

    def _socketStateNotification(self, notification: int) -> None:
        if notification == STATE_DISCONNECTED:
            self.publishStatus("TCP connection lost")
            self.addToTrace("Reinitializing fsmEvse due to connection loss")
            self.reInit()
        elif notification == STATE_LISTENING:
            self.publishStatus("TCP listening")
        elif notification == STATE_CONNECTED:
            self.publishStatus("TCP connected")

    def reInit(self) -> None:
        self.addToTrace("Reinitializing fsmEvse")
        self.state = STATE_WAIT_APP_HANDSHAKE
        self.cyclesInState = 0
        self.rxData = b""
        self.evccid = ""
        self.blChargeStopTrigger = False
        if self.Tcp:
            self.Tcp.resetTheConnection()
        self.publishStatus("Waiting for AppHandshake")

    def stopCharging(self) -> None:
        self.blChargeStopTrigger = True
        self.addToTrace("Charge stop request set")

    def enterState(self, n: int) -> None:
        self.addToTrace(
            f"entering state {n}:{_STATE_NAMES.get(n, '?')} from "
            f"{self.state}:{_STATE_NAMES.get(self.state, '?')}"
        )
        self.state = n
        self.cyclesInState = 0

    def isTooLong(self) -> bool:
        """Per-state timeout for the SECC, aligned with DIN 70121 §9.6.4.

        The SECC's perspective is the mirror of the EVCC:

        * ``V2G_SECC_Sequence_Timeout`` — default 60 s, 5 s for
          CurrentDemand-related states. If the EVCC doesn't advance the
          sequence within this window, the SECC resets the session back
          to the app-handshake state and waits for a new EVCC to connect.
        """
        from .din_spec import (
            seconds_to_cycles,
            V2G_EVCC_SEQUENCE_TIMEOUT_S,
            V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S,
        )
        if self.state == STATE_WAIT_FLEXIBLE:
            # The FlexibleRequest state covers CurrentDemand + everything
            # else, so we pick the tighter 5 s so a stalled CurrentDemand
            # loop doesn't leave the SECC hung forever.
            limit = seconds_to_cycles(V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S)
        else:
            # SessionSetup / ServiceDiscovery / etc. get the default 60 s.
            limit = seconds_to_cycles(V2G_EVCC_SEQUENCE_TIMEOUT_S)
        return self.cyclesInState > limit

    # ---- transmit / receive helpers ---------------------------------

    def _notify(self, direction: str, msg_name: str, params: dict[str, Any]) -> None:
        """Fan-out a decoded message to the optional observer. Never raises."""
        if self.message_observer is None:
            return
        try:
            self.message_observer.on_message(direction, msg_name, params)
        except Exception as e:                                  # noqa: BLE001
            # Observer is diagnostic; never let it break the FSM.
            self.addToTrace(f"[observer error] {e}")

    def _decode_rx(self, exi: bytes | bytearray, schema: str, msg_name_hint: str) -> str:
        """Decode an incoming EXI frame and notify observer.

        ``msg_name_hint`` is the expected message name (e.g. "SessionSetupReq");
        we verify it appears in the decoded JSON before parsing. The raw
        decoded string is returned so the caller can still do substring
        checks. If parsing fails the observer still sees an empty dict
        tagged with the hint.
        """
        decoded = exiDecode(exi, schema)
        if self.message_observer is not None:
            params: dict[str, Any] = {}
            try:
                params = _json.loads(decoded)
            except (ValueError, TypeError):
                pass
            if isinstance(exi, (bytes, bytearray)):
                params["_raw_exi_hex"] = bytes(exi).hex().upper()
            name = params.get("msgName", msg_name_hint) if params else msg_name_hint
            self._notify("rx", name, params)
        return decoded

    def _intercept_and_send(
        self,
        stage: str,
        default_params: dict[str, Any],
        command_builder: Callable[[dict[str, Any]], str],
    ) -> bool:
        """Run ``default_params`` through the pause controller, encode the
        resulting command and transmit it. Returns True on successful send.
        """
        params = self.pause_controller.intercept(stage, default_params)
        cmd = command_builder(params)
        self.addToTrace(f"{stage}: encoding command {cmd}")
        encoded = exiEncode(cmd)
        msg = addV2GTPHeader(encoded)
        self.addToTrace(f"{stage}: responding {prettyHexMessage(msg)}")
        # Best-effort decode for the observer — lets the GUI show exactly what
        # was encoded on the wire, including any GUI-injected modifications.
        # We also stash the raw EXI hex so downstream tools (pcap export,
        # session comparator) can reconstruct wire-level bytes.
        if self.message_observer is not None:
            try:
                decoded_tx = exiDecode(encoded, "DH" if stage == "supportedAppProtocolRes"
                                       else "D" + self.schemaSelection)
                import json as _j
                params_tx = {}
                try:
                    params_tx = _j.loads(decoded_tx)
                except (ValueError, TypeError):
                    pass
                # Always include the raw bytes — downstream tools rely on this.
                params_tx["_raw_exi_hex"] = encoded
                name = params_tx.get("msgName", stage) if params_tx else stage
                self._notify("tx", name, params_tx)
            except Exception as e:                              # noqa: BLE001
                self.addToTrace(f"[observer decode error] {e}")
        return self.Tcp.transmit(bytes(msg)) == 0

    # ---- state handlers --------------------------------------------

    def _state_wait_app_handshake(self) -> None:
        if self.blChargeStopTrigger:
            self.enterState(STATE_STOPPED)
            return
        if not self.rxData:
            return
        self.addToTrace(
            "WaitAppHandshake received " + prettyHexMessage(self.rxData)
        )
        exi = removeV2GTPHeader(self.rxData)
        self.rxData = b""
        decoded = self._decode_rx(exi, "DH", "supportedAppProtocolReq")
        self.addToTrace(decoded)
        if "supportedAppProtocolReq" not in decoded:
            self.addToTrace("Unexpected message in app-handshake state")
            self.reInit()
            return

        # Scan the PEV's offer for every protocol we recognise. We track both
        # DIN 70121 and ISO 15118-2 schema IDs; if both are offered, the
        # ``protocol_preference`` config entry picks the winner.
        din_schema_id: str | None = None
        iso_schema_id: str | None = None
        try:
            d = json.loads(decoded)
            array_len = int(d.get("AppProtocol_arrayLen", "0"))
            for i in range(array_len):
                namespace = d.get(f"NameSpace_{i}", "")
                sid = d.get(f"SchemaID_{i}", "1")
                if "din:70121" in namespace:
                    din_schema_id = sid
                elif "iso:15118:2:2013" in namespace:
                    iso_schema_id = sid
        except (json.JSONDecodeError, ValueError):
            self.addToTrace("Could not parse supportedAppProtocolReq; using defaults")

        # Pick the protocol honouring the operator's preference. The config
        # value uses pyPLC's vocabulary: prefer_din / prefer_iso /
        # din_only / iso15118_2_only.
        try:
            from ..core.config import getConfigValue
            pref = getConfigValue("protocol_preference").strip().lower()
        except (SystemExit, Exception):
            pref = "prefer_din"

        chosen_schema: str | None = None      # "D" = DIN, "1" = ISO 15118-2
        chosen_schema_id: str | None = None
        if pref == "iso15118_2_only" and iso_schema_id is not None:
            chosen_schema, chosen_schema_id = "1", iso_schema_id
        elif pref == "din_only" and din_schema_id is not None:
            chosen_schema, chosen_schema_id = "D", din_schema_id
        elif pref == "prefer_iso" and iso_schema_id is not None:
            chosen_schema, chosen_schema_id = "1", iso_schema_id
        elif pref == "prefer_iso" and din_schema_id is not None:
            chosen_schema, chosen_schema_id = "D", din_schema_id
        elif din_schema_id is not None:
            chosen_schema, chosen_schema_id = "D", din_schema_id
        elif iso_schema_id is not None:
            chosen_schema, chosen_schema_id = "1", iso_schema_id

        if chosen_schema is None:
            # V2G-DC-226 (§9.2): if none of the PEV's offered protocols
            # match what we support, ResponseCode MUST be
            # ``Failed_NoNegotiation`` (= 2) and the response SHALL NOT
            # contain a SchemaID field. HotWire reaches this branch when
            # (a) the PEV offered protocols we don't recognise, or
            # (b) ``protocol_preference = iso15118_2_only`` but the PEV
            # only offered DIN (or vice versa).
            self.addToTrace(
                "No supported protocol in offer "
                f"(din={din_schema_id}, iso={iso_schema_id}, pref={pref!r}); "
                "sending Failed_NoNegotiation per V2G-DC-226"
            )
            from .din_spec import APP_HAND_RC_FAILED_NO_NEGOTIATION

            def build_fail(params: dict[str, Any]) -> str:
                # SchemaID_isUsed = 0 tells the codec to omit the SchemaID
                # field from the encoded response.
                return (
                    f"Eh_{params['ResponseCode']}_{params['SchemaID_isUsed']}"
                )

            self._intercept_and_send(
                "supportedAppProtocolRes",
                {
                    "ResponseCode": APP_HAND_RC_FAILED_NO_NEGOTIATION,
                    "SchemaID_isUsed": 0,
                },
                build_fail,
            )
            self.publishStatus("Protocol negotiation failed")
            # Terminate the session per §9.2.
            self.enterState(STATE_STOPPED)
            return

        def build(params: dict[str, Any]) -> str:
            return (
                f"Eh_{params['ResponseCode']}_{params['SchemaID_isUsed']}_"
                f"{params['SchemaID']}"
            )

        ok = self._intercept_and_send(
            "supportedAppProtocolRes",
            {
                "ResponseCode": 0,            # OK_SuccessfulNegotiation
                "SchemaID_isUsed": 1,
                "SchemaID": chosen_schema_id,
            },
            build,
        )
        if ok:
            self.schemaSelection = chosen_schema
            label = "DIN" if chosen_schema == "D" else "ISO-15118-2"
            self.publishStatus(f"Protocol negotiated ({label})")
            self.enterState(STATE_WAIT_SESSION_SETUP)

    def _state_wait_session_setup(self) -> None:
        if not self.rxData:
            if self.isTooLong():
                self.enterState(STATE_WAIT_APP_HANDSHAKE)
            return
        self.addToTrace(
            "WaitSessionSetup received " + prettyHexMessage(self.rxData)
        )
        exi = removeV2GTPHeader(self.rxData)
        self.rxData = b""
        decoded = self._decode_rx(exi, "D" + self.schemaSelection, "SessionSetupReq")
        self.addToTrace(decoded)
        if "SessionSetupReq" not in decoded:
            return

        try:
            d = json.loads(decoded)
            self.evccid = d.get("EVCCID", "")
            if self.evccid:
                self.callbackShowStatus(self.evccid, "EVCCID")
                self.addToTrace(f"Captured EVCCID = {self.evccid}")
        except json.JSONDecodeError:
            pass

        def build(params: dict[str, Any]) -> str:
            return (
                f"E{self.schemaSelection}a_"
                f"{params['ResponseCode']}_{params['EVSEID']}"
            )

        self._intercept_and_send(
            "SessionSetupRes",
            {"ResponseCode": 1, "EVSEID": DEFAULT_EVSE_ID_HEX},
            build,
        )
        self.publishStatus("Session established")
        self.enterState(STATE_WAIT_SERVICE_DISCOVERY)

    def _state_wait_service_discovery(self) -> None:
        if not self.rxData:
            if self.isTooLong():
                self.enterState(STATE_WAIT_APP_HANDSHAKE)
            return
        self.addToTrace(
            "WaitServiceDiscovery received " + prettyHexMessage(self.rxData)
        )
        exi = removeV2GTPHeader(self.rxData)
        self.rxData = b""
        decoded = self._decode_rx(exi, "D" + self.schemaSelection, "ServiceDiscoveryReq")
        self.addToTrace(decoded)
        if "ServiceDiscoveryReq" not in decoded:
            return

        # EDb accepts 1 positional arg — ResponseCode. Everything else in
        # ServiceDiscoveryRes (payment options, service category, energy
        # transfer type) is hardcoded in the bundled OpenV2G codec and not
        # operator-overrideable until we ship a patched codec.
        self._intercept_and_send(
            "ServiceDiscoveryRes",
            {"ResponseCode": 0},
            lambda p: f"E{self.schemaSelection}b_{p['ResponseCode']}",
        )
        self.publishStatus("Service discovery done")
        self.enterState(STATE_WAIT_SERVICE_PAYMENT)

    def _state_wait_service_payment(self) -> None:
        if not self.rxData:
            if self.isTooLong():
                self.enterState(STATE_WAIT_APP_HANDSHAKE)
            return
        self.addToTrace(
            "WaitServicePayment received " + prettyHexMessage(self.rxData)
        )
        exi = removeV2GTPHeader(self.rxData)
        self.rxData = b""
        decoded = self._decode_rx(
            exi, "D" + self.schemaSelection, "ServicePaymentSelectionReq"
        )
        self.addToTrace(decoded)
        if (
            "ServicePaymentSelectionReq" not in decoded
            and "PaymentServiceSelectionReq" not in decoded
        ):
            return

        # EDc accepts 1 positional arg — ResponseCode.
        self._intercept_and_send(
            "ServicePaymentSelectionRes",
            {"ResponseCode": 0},
            lambda p: f"E{self.schemaSelection}c_{p['ResponseCode']}",
        )
        self.publishStatus("Payment selection done")
        self.enterState(STATE_WAIT_FLEXIBLE)

    def _state_wait_flexible(self) -> None:
        if not self.rxData:
            return
        self.addToTrace(
            "WaitFlexible received " + prettyHexMessage(self.rxData)
        )
        exi = removeV2GTPHeader(self.rxData)
        self.rxData = b""
        decoded = self._decode_rx(exi, "D" + self.schemaSelection, "FlexibleReq")
        self.addToTrace(decoded)

        if "ContractAuthenticationReq" in decoded:
            # OpenV2G's EDl command encodes: param0=1 → Finished, param0=0 → Ongoing.
            self._intercept_and_send(
                "ContractAuthenticationRes",
                {"EVSEProcessing": 1, "ResponseCode": 0},
                lambda p: (
                    f"E{self.schemaSelection}l_"
                    f"{p['EVSEProcessing']}_{p['ResponseCode']}"
                ),
            )
            self.publishStatus("Contract authenticated")
            return

        if "ChargeParameterDiscoveryReq" in decoded:
            # EDe accepts 27 positional args (legacy fsmEvse.py:659-672):
            #   rc, proc, saslUsed, saslLen, schedStart, pMax,
            #   isoUsed, isoStatus, statusCode, notifDelay, notifType,
            #   maxI_mult, maxI_val, maxI_unit,
            #   maxP_used, maxP_mult, maxP_val, maxP_unit,
            #   maxV_mult, maxV_val, maxV_unit,
            #   minI_mult, minI_val, minI_unit,
            #   minV_mult, minV_val, minV_unit.
            def build_cpd(p: dict[str, Any]) -> str:
                parts = [
                    str(p.get("ResponseCode", 0)),
                    str(p.get("EVSEProcessing", 0)),                 # Finished
                    str(p.get("SAScheduleList_isUsed", 1)),
                    str(p.get("SAScheduleListArrayLen", 1)),
                    str(p.get("SchedTupleStart", 0)),
                    str(p.get("PMax", 50)),
                    str(p.get("IsolationStatusUsed", 1)),
                    str(p.get("IsolationStatus", 0)),                # Invalid at CPD time
                    str(p.get("EVSEStatusCode", 1)),                 # EVSE_Ready
                    str(p.get("NotificationMaxDelay", 0)),
                    str(p.get("EVSENotification", 0)),
                    # Max current limit.
                    str(p.get("EVSEMaximumCurrentLimitMultiplier", 0)),
                    str(p.get("EVSEMaximumCurrentLimit", 200)),
                    str(p.get("EVSEMaximumCurrentLimitUnit", 3)),
                    # Max power limit.
                    str(p.get("EVSEMaximumPowerLimit_isUsed", 1)),
                    str(p.get("EVSEMaximumPowerLimitMultiplier", 3)),
                    str(p.get("EVSEMaximumPowerLimit", 10)),
                    str(p.get("EVSEMaximumPowerLimitUnit", 7)),
                    # Max voltage limit.
                    str(p.get("EVSEMaximumVoltageLimitMultiplier", 0)),
                    str(p.get("EVSEMaximumVoltageLimit", 450)),
                    str(p.get("EVSEMaximumVoltageLimitUnit", 5)),
                    # Min current / voltage.
                    str(p.get("EVSEMinimumCurrentLimitMultiplier", 0)),
                    str(p.get("EVSEMinimumCurrentLimit", 1)),
                    str(p.get("EVSEMinimumCurrentLimitUnit", 3)),
                    str(p.get("EVSEMinimumVoltageLimitMultiplier", 0)),
                    str(p.get("EVSEMinimumVoltageLimit", 200)),
                    str(p.get("EVSEMinimumVoltageLimitUnit", 5)),
                ]
                return f"E{self.schemaSelection}e_" + "_".join(parts)

            self._intercept_and_send(
                "ChargeParameterDiscoveryRes",
                {},
                build_cpd,
            )
            self.publishStatus("Charge parameters sent")
            return

        if "CableCheckReq" in decoded:
            # OpenV2G EDf arg order (6 positional):
            #   proc, statusCode, isolationStatus, isolationUsed,
            #   notificationMaxDelay, evseNotification.
            # DIN Table 34 §9.4.2.2 lists DC_EVSEStatus.NotificationMaxDelay
            # and EVSENotification as MANDATORY fields of DC_EVSEStatusType
            # (§9.5.3 Table 66), so we emit them explicitly rather than
            # letting the codec pick its defaults.
            def build(p: dict[str, Any]) -> str:
                return (
                    f"E{self.schemaSelection}f_"
                    f"{p['EVSEProcessing']}_{p['EVSEStatusCode']}_"
                    f"{p['IsolationStatus']}_{p['IsolationStatusUsed']}_"
                    f"{p['NotificationMaxDelay']}_{p['EVSENotification']}"
                )

            self._intercept_and_send(
                "CableCheckRes",
                {
                    "EVSEProcessing": 0,            # Finished
                    "EVSEStatusCode": 1,            # EVSE_Ready
                    "IsolationStatus": 1,           # Valid
                    "IsolationStatusUsed": 1,
                    "NotificationMaxDelay": 0,      # V2G-DC-636: EVCC ignores this
                    "EVSENotification": 0,          # None — V2G-DC-500 recommendation
                },
                build,
            )
            self.publishStatus("Cable check done")
            return

        if "PreChargeReq" in decoded:
            # Report the EV's target voltage back as PresentVoltage so the PEV
            # exits the precharge loop immediately (simulation shortcut).
            target_v = 350
            try:
                d = json.loads(decoded)
                ev_tv = d.get("EVTargetVoltage", {})
                if isinstance(ev_tv, dict):
                    v = int(ev_tv.get("Value", 350))
                    m = int(ev_tv.get("Multiplier", 0))
                    target_v = max(1, int(v * (10 ** m)))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

            # OpenV2G EDg arg order (7 positional):
            #   EVSEPresentVoltage, responseCode, isolationUsed,
            #   isolationStatus, statusCode, notifDelay, notifType.
            # DIN Table 46 §9.4.2.6 + Table 66 require all DC_EVSEStatus
            # fields to be explicit — we stop relying on codec defaults.
            def build(p: dict[str, Any]) -> str:
                return (
                    f"E{self.schemaSelection}g_"
                    f"{int(p['EVSEPresentVoltage'])}_"
                    f"{p['ResponseCode']}_"
                    f"{p['IsolationStatusUsed']}_{p['IsolationStatus']}_"
                    f"{p['EVSEStatusCode']}_"
                    f"{p['NotificationMaxDelay']}_{p['EVSENotification']}"
                )

            self._intercept_and_send(
                "PreChargeRes",
                {
                    "EVSEPresentVoltage": target_v,
                    "ResponseCode": 0,              # OK
                    "IsolationStatusUsed": 1,
                    "IsolationStatus": 1,           # Valid
                    "EVSEStatusCode": 1,            # EVSE_Ready
                    "NotificationMaxDelay": 0,
                    "EVSENotification": 0,          # None
                },
                build,
            )
            self.publishStatus("Pre-charging")
            return

        if "PowerDeliveryReq" in decoded:
            # EDh arg order (6 positional): rc, isoUsed, isoStatus,
            # statusCode, notifDelay, notifType. Always emit the full
            # form so the mandatory DC_EVSEStatus.NotificationMaxDelay /
            # EVSENotification fields are explicit (Table 66).
            def build_pd(p: dict[str, Any]) -> str:
                return (
                    f"E{self.schemaSelection}h_"
                    f"{p['ResponseCode']}_{p['IsolationStatusUsed']}_"
                    f"{p['IsolationStatus']}_{p['EVSEStatusCode']}_"
                    f"{p['NotificationMaxDelay']}_{p['EVSENotification']}"
                )

            self._intercept_and_send(
                "PowerDeliveryRes",
                {
                    "ResponseCode": 0,              # OK
                    "IsolationStatusUsed": 1,
                    "IsolationStatus": 1,           # Valid
                    "EVSEStatusCode": 1,            # EVSE_Ready
                    "NotificationMaxDelay": 0,
                    "EVSENotification": 0,          # None
                },
                build_pd,
            )
            self.publishStatus("Power delivery")
            return

        if "CurrentDemandReq" in decoded:
            # EDi has 27 positional args. DIN §9.4.2.8 Table 48 +
            # V2G-DC-948/949/950 mark all three EVSEMaximum*Limit elements
            # as MANDATORY for DIN 70121 (they are optional in ISO
            # 15118-2). So the "_isUsed" flags default to 1 here, and we
            # supply plausible defaults drawn from din_spec.
            from .din_spec import (
                CURRENT_DEMAND_RES_MAX_V_DEFAULT,
                CURRENT_DEMAND_RES_MAX_I_DEFAULT,
                CURRENT_DEMAND_RES_MAX_P_POWER,
                CURRENT_DEMAND_RES_MAX_P_MULTIPLIER,
            )

            def build_cd(p: dict[str, Any]) -> str:
                # Always emit the full 27-arg form — even without operator
                # overrides — so DIN's mandatory EVSEMaximum*Limit fields
                # (V2G-DC-948/949/950) are present on the wire. The bare
                # "EDi" encoding would omit them.
                parts = [
                    str(p.get("ResponseCode", 0)),
                    str(p.get("IsolationStatusUsed", 1)),
                    str(p.get("IsolationStatus", 1)),
                    str(p.get("EVSEStatusCode", 1)),
                    str(p.get("NotificationMaxDelay", 0)),
                    str(p.get("EVSENotification", 0)),
                    str(p.get("EVSEPresentVoltageMultiplier", 0)),
                    str(p.get("EVSEPresentVoltage", 400)),
                    str(p.get("EVSEPresentVoltageUnit", 5)),   # V
                    str(p.get("EVSEPresentCurrentMultiplier", 0)),
                    str(p.get("EVSEPresentCurrent", 50)),
                    str(p.get("EVSEPresentCurrentUnit", 3)),   # A
                    str(p.get("EVSECurrentLimitAchieved", 0)),
                    str(p.get("EVSEVoltageLimitAchieved", 0)),
                    str(p.get("EVSEPowerLimitAchieved", 0)),
                    # Max-voltage limit — MANDATORY in DIN 70121.
                    str(p.get("EVSEMaximumVoltageLimit_isUsed", 1)),
                    str(p.get("EVSEMaximumVoltageLimitMultiplier", 0)),
                    str(p.get("EVSEMaximumVoltageLimit",
                              CURRENT_DEMAND_RES_MAX_V_DEFAULT)),
                    str(p.get("EVSEMaximumVoltageLimitUnit", 5)),   # V
                    # Max-current limit — MANDATORY in DIN 70121.
                    str(p.get("EVSEMaximumCurrentLimit_isUsed", 1)),
                    str(p.get("EVSEMaximumCurrentLimitMultiplier", 0)),
                    str(p.get("EVSEMaximumCurrentLimit",
                              CURRENT_DEMAND_RES_MAX_I_DEFAULT)),
                    str(p.get("EVSEMaximumCurrentLimitUnit", 3)),   # A
                    # Max-power limit — MANDATORY in DIN 70121.
                    str(p.get("EVSEMaximumPowerLimit_isUsed", 1)),
                    str(p.get("EVSEMaximumPowerLimitMultiplier",
                              CURRENT_DEMAND_RES_MAX_P_MULTIPLIER)),
                    str(p.get("EVSEMaximumPowerLimit",
                              CURRENT_DEMAND_RES_MAX_P_POWER)),
                    str(p.get("EVSEMaximumPowerLimitUnit", 7)),     # W
                ]
                return f"E{self.schemaSelection}i_" + "_".join(parts)

            self._intercept_and_send("CurrentDemandRes", {}, build_cd)
            return

        if "WeldingDetectionReq" in decoded:
            # EDj accepts 9 args: rc, isoUsed, isoStatus, statusCode,
            # notifDelay, notifType, presentV_mult, presentV_val, presentV_unit.
            def build_wd(p: dict[str, Any]) -> str:
                return (
                    f"E{self.schemaSelection}j_"
                    f"{p.get('ResponseCode', 0)}_"
                    f"{p.get('IsolationStatusUsed', 1)}_"
                    f"{p.get('IsolationStatus', 1)}_"
                    f"{p.get('EVSEStatusCode', 1)}_"
                    f"{p.get('NotificationMaxDelay', 0)}_"
                    f"{p.get('EVSENotification', 0)}_"
                    f"{p.get('EVSEPresentVoltageMultiplier', 0)}_"
                    f"{p.get('EVSEPresentVoltage', 0)}_"
                    f"{p.get('EVSEPresentVoltageUnit', 5)}"
                )

            self._intercept_and_send(
                "WeldingDetectionRes",
                {},
                build_wd,
            )
            self.publishStatus("Welding detection")
            return

        if "SessionStopReq" in decoded:
            # EDk accepts 1 arg — ResponseCode.
            self._intercept_and_send(
                "SessionStopRes",
                {"ResponseCode": 0},
                lambda p: f"E{self.schemaSelection}k_{p['ResponseCode']}",
            )
            self.publishStatus("Session stopped")
            self.enterState(STATE_STOPPED)
            return

        self.addToTrace("FlexibleRequest: unrecognized message — ignoring")

    def _state_stopped(self) -> None:
        if self.rxData:
            self.addToTrace(
                "Ignoring message in Stopped state: " + prettyHexMessage(self.rxData)
            )
            self.rxData = b""

    # ---- dispatch table --------------------------------------------

    _dispatch = {
        STATE_WAIT_APP_HANDSHAKE: _state_wait_app_handshake,
        STATE_WAIT_SESSION_SETUP: _state_wait_session_setup,
        STATE_WAIT_SERVICE_DISCOVERY: _state_wait_service_discovery,
        STATE_WAIT_SERVICE_PAYMENT: _state_wait_service_payment,
        STATE_WAIT_FLEXIBLE: _state_wait_flexible,
        STATE_STOPPED: _state_stopped,
    }

    def mainfunction(self) -> None:
        if self.Tcp is None:
            self.addToTrace("Error: TCP socket not initialized")
            return
        self.Tcp.mainfunction()
        if self.Tcp.isRxDataAvailable():
            self.rxData = self.Tcp.getRxData()
        self.cyclesInState += 1
        handler = self._dispatch.get(self.state)
        if handler is None:
            self.addToTrace(f"Unknown state {self.state}; resetting")
            self.reInit()
            return
        handler(self)
