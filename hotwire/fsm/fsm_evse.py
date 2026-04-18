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
        return self.cyclesInState > 100

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

        chosen_schema = "D"           # "D" = DIN, "1" = ISO 15118-2
        chosen_schema_id = "1"
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
        # Else: neither protocol was offered in a way we recognised — we'll
        # still respond with DIN schema ID 1, and the PEV will almost
        # certainly reject it. That's the correct failure mode.

        def build(params: dict[str, Any]) -> str:
            return (
                f"Eh_{params['ResponseCode']}_{params['SchemaID_isUsed']}_"
                f"{params['SchemaID']}"
            )

        ok = self._intercept_and_send(
            "supportedAppProtocolRes",
            {
                "ResponseCode": 0,
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

        self._intercept_and_send(
            "ServiceDiscoveryRes",
            {},
            lambda _p: f"E{self.schemaSelection}b",  # bare-default OK
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

        self._intercept_and_send(
            "ServicePaymentSelectionRes",
            {},
            lambda _p: f"E{self.schemaSelection}c",
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
            self._intercept_and_send(
                "ChargeParameterDiscoveryRes",
                {},
                lambda _p: f"E{self.schemaSelection}e",
            )
            self.publishStatus("Charge parameters sent")
            return

        if "CableCheckReq" in decoded:
            # OpenV2G EDf param order: proc, statusCode, isolationStatus, isolationUsed.
            # proc=0 → Finished, statusCode=1 → EVSE_Ready, isolationStatus=1 → Valid.
            def build(p: dict[str, Any]) -> str:
                return (
                    f"E{self.schemaSelection}f_"
                    f"{p['EVSEProcessing']}_{p['EVSEStatusCode']}_"
                    f"{p['IsolationStatus']}_{p['IsolationStatusUsed']}"
                )

            self._intercept_and_send(
                "CableCheckRes",
                {
                    "EVSEProcessing": 0,
                    "EVSEStatusCode": 1,
                    "IsolationStatus": 1,
                    "IsolationStatusUsed": 1,
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

            self._intercept_and_send(
                "PreChargeRes",
                {"EVSEPresentVoltage": target_v},
                lambda p: f"E{self.schemaSelection}g_{int(p['EVSEPresentVoltage'])}",
            )
            self.publishStatus("Pre-charging")
            return

        if "PowerDeliveryReq" in decoded:
            # EDh param order: rc, isoUsed, isoStatus, statusCode, notifDelay, notifType.
            # If the FSM has been given any override at all we inject the full
            # form; otherwise OpenV2G's bare "EDh" produces a conformant default
            # PowerDeliveryRes and we leave it alone (the prebuilt codec's
            # custom-params mode handles both paths).
            def build_pd(p: dict[str, Any]) -> str:
                if not p:
                    return f"E{self.schemaSelection}h"
                return (
                    f"E{self.schemaSelection}h_"
                    f"{p['ResponseCode']}_{p['IsolationStatusUsed']}_"
                    f"{p['IsolationStatus']}_{p['EVSEStatusCode']}_"
                    f"{p['NotificationMaxDelay']}_{p['EVSENotification']}"
                )

            self._intercept_and_send(
                "PowerDeliveryRes",
                {},   # empty default — FSM sends bare EDh unless overrides exist
                build_pd,
            )
            self.publishStatus("Power delivery")
            return

        if "CurrentDemandReq" in decoded:
            # EDi has 27 positional args — any override forces us into the full
            # form, so every field gets a sane default even if the playbook
            # only cares about one or two (typical case: EVSEPresentVoltage
            # / EVSEPresentCurrent for sustained-discharge spoofing).
            def build_cd(p: dict[str, Any]) -> str:
                if not p:
                    return f"E{self.schemaSelection}i"
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
                    # Max-voltage limit (unused by default).
                    str(p.get("EVSEMaximumVoltageLimit_isUsed", 0)),
                    str(p.get("EVSEMaximumVoltageLimitMultiplier", 0)),
                    str(p.get("EVSEMaximumVoltageLimit", 0)),
                    str(p.get("EVSEMaximumVoltageLimitUnit", 5)),
                    # Max-current limit (unused by default).
                    str(p.get("EVSEMaximumCurrentLimit_isUsed", 0)),
                    str(p.get("EVSEMaximumCurrentLimitMultiplier", 0)),
                    str(p.get("EVSEMaximumCurrentLimit", 0)),
                    str(p.get("EVSEMaximumCurrentLimitUnit", 3)),
                    # Max-power limit (unused by default).
                    str(p.get("EVSEMaximumPowerLimit_isUsed", 0)),
                    str(p.get("EVSEMaximumPowerLimitMultiplier", 0)),
                    str(p.get("EVSEMaximumPowerLimit", 0)),
                    str(p.get("EVSEMaximumPowerLimitUnit", 7)),  # W
                ]
                return f"E{self.schemaSelection}i_" + "_".join(parts)

            self._intercept_and_send("CurrentDemandRes", {}, build_cd)
            return

        if "WeldingDetectionReq" in decoded:
            self._intercept_and_send(
                "WeldingDetectionRes",
                {},
                lambda _p: f"E{self.schemaSelection}j",
            )
            self.publishStatus("Welding detection")
            return

        if "SessionStopReq" in decoded:
            self._intercept_and_send(
                "SessionStopRes",
                {},
                lambda _p: f"E{self.schemaSelection}k",
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
