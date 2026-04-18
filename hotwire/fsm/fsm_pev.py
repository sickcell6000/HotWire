"""
DIN 70121 PEV (electric vehicle) state machine.

Modernized port of the legacy ``archive/legacy-evse/fsmPev.py`` (902 lines,
pyPLC GPL-3.0). Drives the EV side of the charging session:

    TCP connect -> SupportedAppProtocolReq -> SessionSetupReq ->
    ServiceDiscoveryReq -> ServicePaymentSelectionReq ->
    ContractAuthenticationReq (repeated until Finished) ->
    ChargeParameterDiscoveryReq -> (connector lock) ->
    CableCheckReq -> PreChargeReq -> PowerDeliveryReq (ON) ->
    CurrentDemandReq loop -> PowerDeliveryReq (OFF) ->
    WeldingDetectionReq -> SessionStopReq

Every outbound Req passes through ``PauseController.intercept`` so the GUI
layer (Checkpoint 3) can edit EVCCID, voltage/current targets, etc.

Adapted from pyPLC's fsmPev.py (GPL-3.0, uhi22).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ..core.config import getConfigValue, getConfigValueBool
from ..exi.connector import (
    addV2GTPHeader,
    exiDecode,
    exiEncode,
    exiHexToByteArray,
    removeV2GTPHeader,
)
from ..helpers import prettyHexMessage
from ..plc.tcp_socket import pyPlcTcpClientSocket
import json as _json

from .constants import DEFAULT_SESSION_ID_HEX, is_error_evse_status_code
from .message_observer import MessageObserver
from .pause_controller import PauseController


# --- Pre-captured supportedAppProtocolReq blobs ---
# Each blob advertises a specific protocol set so we can force the EVSE
# into a particular negotiation outcome.

# Hyundai Ioniq (DIN 70121 only).
EXI_DEMO_SUPPORTED_APP_PROTOCOL_IONIQ = (
    "8000dbab9371d3234b71d1b981899189d191818991d26b9b3a232b30020000040040"
)

# Tesla Model Y (DIN 70121 + Tesla-proprietary extension).
EXI_DEMO_SUPPORTED_APP_PROTOCOL_TESLA = (
    "8000DBAB9371D3234B71D1B981899189D191818991D26B9B3A232B30020000040401B"
    "75726E3A7465736C613A64696E3A323031383A4D736744656600001C0100080"
)


def _iso_capable_app_protocol_hex() -> str | None:
    """Generate an ISO 15118-2 + DIN 70121 capable supportedAppProtocolReq.

    The bundled OpenV2G codec's ``EH_<schemaID>`` command can emit a
    handshake request that advertises a single schema ID — that's the
    closest thing it has to ISO support on the *request* side. It does
    NOT have a way to emit a multi-protocol offer via command-line
    syntax, and its ``E1A_...`` (ISO SessionSetupReq) encode path also
    refuses to accept parameters.

    Consequence: HotWire's **PEV side cannot actually drive an ISO
    15118-2 session** until either (a) we capture a real ISO-capable
    ``supportedAppProtocolReq`` blob from an EV on the wire and bake it
    in here, or (b) we patch OpenV2G to accept ``E1A_<evccid>``. Both
    are Checkpoint 7+ work.

    This function is left in place as a future extension point. It
    currently always returns ``None``, which tells the caller to fall
    back to the DIN-only Ioniq blob.
    """
    # Return None until one of the extension paths above lands.
    return None


# --- State numbers -------------------------------------------------------

STATE_NOT_INITIALIZED = 0
STATE_CONNECTING = 1
STATE_CONNECTED = 2
STATE_WAIT_APP_RES = 3
STATE_WAIT_SESSION_SETUP_RES = 4
STATE_WAIT_SERVICE_DISCOVERY_RES = 5
STATE_WAIT_SERVICE_PAYMENT_RES = 6
STATE_WAIT_CONTRACT_AUTH_RES = 7
STATE_WAIT_CHARGE_PARAM_RES = 8
STATE_WAIT_CONNECTOR_LOCK = 9
STATE_WAIT_CABLE_CHECK_RES = 10
STATE_WAIT_PRECHARGE_RES = 11
STATE_WAIT_CONTACTORS_CLOSED = 12
STATE_WAIT_POWER_DELIVERY_RES = 13
STATE_WAIT_CURRENT_DEMAND_RES = 14
STATE_WAIT_WELDING_RES = 15
STATE_WAIT_SESSION_STOP_RES = 16
STATE_CHARGING_FINISHED = 17
STATE_UNRECOVERABLE_ERROR = 88
STATE_SEQUENCE_TIMEOUT = 99
STATE_SAFE_SHUTDOWN_WAIT_CHARGER = 111
STATE_SAFE_SHUTDOWN_WAIT_CONTACTORS = 222
STATE_END = 1000


_STATE_NAMES = {
    STATE_NOT_INITIALIZED: "NotYetInitialized",
    STATE_CONNECTING: "Connecting",
    STATE_CONNECTED: "Connected",
    STATE_WAIT_APP_RES: "WaitForAppProtocolRes",
    STATE_WAIT_SESSION_SETUP_RES: "WaitForSessionSetupRes",
    STATE_WAIT_SERVICE_DISCOVERY_RES: "WaitForServiceDiscoveryRes",
    STATE_WAIT_SERVICE_PAYMENT_RES: "WaitForServicePaymentRes",
    STATE_WAIT_CONTRACT_AUTH_RES: "WaitForContractAuthRes",
    STATE_WAIT_CHARGE_PARAM_RES: "WaitForChargeParamRes",
    STATE_WAIT_CONNECTOR_LOCK: "WaitForConnectorLock",
    STATE_WAIT_CABLE_CHECK_RES: "WaitForCableCheckRes",
    STATE_WAIT_PRECHARGE_RES: "WaitForPreChargeRes",
    STATE_WAIT_CONTACTORS_CLOSED: "WaitForContactorsClosed",
    STATE_WAIT_POWER_DELIVERY_RES: "WaitForPowerDeliveryRes",
    STATE_WAIT_CURRENT_DEMAND_RES: "WaitForCurrentDemandRes",
    STATE_WAIT_WELDING_RES: "WaitForWeldingRes",
    STATE_WAIT_SESSION_STOP_RES: "WaitForSessionStopRes",
    STATE_CHARGING_FINISHED: "ChargingFinished",
    STATE_UNRECOVERABLE_ERROR: "UnrecoverableError",
    STATE_SEQUENCE_TIMEOUT: "SequenceTimeout",
    STATE_SAFE_SHUTDOWN_WAIT_CHARGER: "SafeShutdownWaitCharger",
    STATE_SAFE_SHUTDOWN_WAIT_CONTACTORS: "SafeShutdownWaitContactors",
    STATE_END: "End",
}


class fsmPev:
    """PEV-side DIN 70121 state machine."""

    def __init__(
        self,
        addressManager,
        connMgr,
        callbackAddToTrace: Callable[[str], None],
        hardwareInterface,
        callbackShowStatus: Callable[[str, str, str, str], None],
        pause_controller: Optional[PauseController] = None,
        message_observer: Optional[MessageObserver] = None,
        preferred_protocol: str = "din",
    ) -> None:
        """``preferred_protocol`` picks which pre-captured
        supportedAppProtocolReq blob to send: ``"din"`` (Ioniq-style DIN
        70121 only), ``"iso"`` (offer ISO 15118-2; requires a codec with
        custom-params ``EH_`` encode support), or ``"both"`` (advertise
        both protocols and let the EVSE pick per its preference)."""
        self.addressManager = addressManager
        self.connMgr = connMgr
        self.callbackAddToTrace = callbackAddToTrace
        self.hardwareInterface = hardwareInterface
        self.callbackShowStatus = callbackShowStatus
        self.pause_controller = pause_controller or PauseController()
        self.message_observer = message_observer
        self.preferred_protocol = preferred_protocol.lower()

        self.addToTrace("Initializing fsmPev")
        self.Tcp = pyPlcTcpClientSocket(self.callbackAddToTrace)

        self.state = STATE_NOT_INITIALIZED
        self.sessionId = DEFAULT_SESSION_ID_HEX
        self.evccid = addressManager.getLocalMacAsTwelfCharString()
        self.cyclesInState = 0
        self.DelayCycles = 0
        self.rxData: bytes = b""
        try:
            self.isLightBulbDemo = getConfigValueBool("light_bulb_demo")
        except SystemExit:
            self.isLightBulbDemo = False
        self.isBulbOn = False
        self.cyclesLightBulbDelay = 0
        self.isUserStopRequest = False
        self.wasPowerDeliveryRequestedOn = False
        self.numberOfContractAuthenticationReq = 0
        self.numberOfChargeParameterDiscoveryReq = 0
        self.numberOfCableCheckReq = 0

    # ---- logging / status ------------------------------------------

    def addToTrace(self, s: str) -> None:
        self.callbackAddToTrace("[PEV] " + s)

    def publishStatus(self, s: str, aux1: str = "", aux2: str = "") -> None:
        self.callbackShowStatus(s, "pevState", aux1, aux2)

    # ---- lifecycle --------------------------------------------------

    def reInit(self) -> None:
        self.addToTrace("re-initializing fsmPev")
        self.Tcp.disconnect()
        self.hardwareInterface.setStateB()
        self.hardwareInterface.setPowerRelayOff()
        self.hardwareInterface.setRelay2Off()
        self.isBulbOn = False
        self.cyclesLightBulbDelay = 0
        self.state = STATE_CONNECTING
        self.cyclesInState = 0
        self.rxData = b""

    def stopCharging(self) -> None:
        self.isUserStopRequest = True

    def enterState(self, n: int) -> None:
        self.addToTrace(
            f"from {self.state}:{_STATE_NAMES.get(self.state, '?')} "
            f"entering {n}:{_STATE_NAMES.get(n, '?')}"
        )
        self.state = n
        self.cyclesInState = 0

    def isTooLong(self) -> bool:
        """Per-state timeout, aligned with DIN 70121 §9.6 / Tables 76 & 78.

        The standard distinguishes four kinds of timer:

        * ``V2G_EVCC_Msg_Timeout``  — per-request-response pair, default 2 s,
          but 0.5 s for CurrentDemand (§9.6.3.1).
        * ``V2G_EVCC_SequenceTimeout`` — total time in a phase, default 60 s,
          5 s for CurrentDemand (§9.6.3.2).
        * ``V2G_EVCC_Ongoing_Timeout``  — how long an "Ongoing" re-request
          loop may run. 60 s in DIN.
        * Phase-specific wall-clock timers (Table 78): CableCheck 38 s,
          PreCharge 7 s, ReadyToCharge 10 s, CommunicationSetup 20 s.

        HotWire maps each FSM state to whichever timer is most restrictive
        for that state's role.
        """
        from .din_spec import (
            seconds_to_cycles,
            V2G_EVCC_MSG_TIMEOUT_DEFAULT_S,
            V2G_EVCC_MSG_TIMEOUT_CURRENT_DEMAND_S,
            V2G_EVCC_SEQUENCE_TIMEOUT_S,
            V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S,
            V2G_EVCC_COMMUNICATION_SETUP_TIMEOUT_S,
            V2G_EVCC_READY_TO_CHARGE_TIMEOUT_S,
            V2G_EVCC_CABLE_CHECK_TIMEOUT_S,
            V2G_EVCC_PRE_CHARGE_TIMEOUT_S,
        )

        # Default: any Req-Res pair gets Msg_Timeout (2 s).
        limit = seconds_to_cycles(V2G_EVCC_MSG_TIMEOUT_DEFAULT_S)

        # Phases that run "Ongoing" re-request loops — allow up to the
        # sequence timeout (60 s) because the whole phase may span many
        # individual requests.
        if self.state in (
            STATE_WAIT_CONTRACT_AUTH_RES,
            STATE_WAIT_CHARGE_PARAM_RES,
        ):
            limit = seconds_to_cycles(V2G_EVCC_SEQUENCE_TIMEOUT_S)

        # CableCheck has its own 38 s phase timeout (Table 78).
        elif self.state == STATE_WAIT_CABLE_CHECK_RES:
            limit = seconds_to_cycles(V2G_EVCC_CABLE_CHECK_TIMEOUT_S)

        # PreCharge has a 7 s phase timeout (Table 78).
        elif self.state == STATE_WAIT_PRECHARGE_RES:
            limit = seconds_to_cycles(V2G_EVCC_PRE_CHARGE_TIMEOUT_S)

        # PowerDelivery fits under the general 10 s ReadyToCharge limit.
        elif self.state == STATE_WAIT_POWER_DELIVERY_RES:
            limit = seconds_to_cycles(V2G_EVCC_READY_TO_CHARGE_TIMEOUT_S)

        # ContactorsClosed is a local hardware wait; cap at
        # ReadyToCharge_Timeout to keep total phase time bounded.
        elif self.state == STATE_WAIT_CONTACTORS_CLOSED:
            limit = seconds_to_cycles(V2G_EVCC_READY_TO_CHARGE_TIMEOUT_S)

        # CurrentDemand has its own tight 0.5 s Msg_Timeout. Using that
        # bare would mean aborting the session if a single CurrentDemand
        # reply is late by 500 ms, which is too strict for a simulator
        # running under a shared GIL. We use 5 s sequence_timeout instead
        # — this matches the standard's explicit CurrentDemand sequence
        # timeout from §9.6.3.2.
        elif self.state == STATE_WAIT_CURRENT_DEMAND_RES:
            limit = seconds_to_cycles(V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S)

        # The TCP-connect + app-handshake opening window.
        elif self.state in (
            STATE_CONNECTING,
            STATE_CONNECTED,
            STATE_WAIT_APP_RES,
        ):
            limit = seconds_to_cycles(V2G_EVCC_COMMUNICATION_SETUP_TIMEOUT_S)

        return self.cyclesInState > limit

    # ---- transmit / receive helpers -------------------------------

    def _notify(self, direction: str, msg_name: str, params: dict[str, Any]) -> None:
        if self.message_observer is None:
            return
        try:
            self.message_observer.on_message(direction, msg_name, params)
        except Exception as e:                                  # noqa: BLE001
            self.addToTrace(f"[observer error] {e}")

    def _intercept_and_send(
        self,
        stage: str,
        default_params: dict[str, Any],
        command_builder: Callable[[dict[str, Any]], str],
    ) -> bool:
        params = self.pause_controller.intercept(stage, default_params)
        cmd = command_builder(params)
        self.addToTrace(f"{stage}: encoding command {cmd}")
        encoded = exiEncode(cmd)
        msg = addV2GTPHeader(encoded)
        self.addToTrace(f"{stage}: sending {prettyHexMessage(msg)}")
        # Observer hook — decode the command we just built so the GUI can
        # display the wire-level outcome, including any GUI modifications.
        if self.message_observer is not None:
            schema = "Dh" if stage == "supportedAppProtocolReq" else "DD"
            try:
                decoded_tx = exiDecode(encoded, schema)
                params_tx: dict[str, Any] = {}
                try:
                    params_tx = _json.loads(decoded_tx)
                except (ValueError, TypeError):
                    pass
                params_tx["_raw_exi_hex"] = encoded
                name = params_tx.get("msgName", stage) if params_tx else stage
                self._notify("tx", name, params_tx)
            except Exception as e:                              # noqa: BLE001
                self.addToTrace(f"[observer decode error] {e}")
        return self.Tcp.transmit(bytes(msg)) == 0

    def _decode(self, exi: bytes | bytearray, schema: str) -> str:
        self.connMgr.ApplOk()
        decoded = exiDecode(exi, schema)
        if self.message_observer is not None:
            params: dict[str, Any] = {}
            try:
                params = _json.loads(decoded)
            except (ValueError, TypeError):
                pass
            if isinstance(exi, (bytes, bytearray)):
                params["_raw_exi_hex"] = bytes(exi).hex().upper()
            name = params.get("msgName", "UnknownRes") if params else "UnknownRes"
            self._notify("rx", name, params)
        return decoded

    # ---- shared Req builders --------------------------------------

    def _soc_str(self) -> str:
        return str(int(self.hardwareInterface.getSoc()))

    def _send_contract_auth_req(self) -> None:
        self._intercept_and_send(
            "ContractAuthenticationReq",
            {"SessionID": self.sessionId},
            lambda p: f"EDL_{p['SessionID']}",
        )

    def _send_charge_parameter_discovery_req(self) -> None:
        self._intercept_and_send(
            "ChargeParameterDiscoveryReq",
            {"SessionID": self.sessionId, "SoC": self._soc_str()},
            lambda p: f"EDE_{p['SessionID']}_{p['SoC']}",
        )

    def _send_cable_check_req(self) -> None:
        self._intercept_and_send(
            "CableCheckReq",
            {"SessionID": self.sessionId, "SoC": self._soc_str()},
            lambda p: f"EDF_{p['SessionID']}_{p['SoC']}",
        )
        self.connMgr.ApplOk(31)

    def _send_precharge_req(self) -> None:
        # DIN Table 45 (V2G-DC-884) mandates EVTargetVoltage + EVTargetCurrent
        # in every PreChargeReq. The bundled OpenV2G codec always emits
        # EVTargetCurrent = 1 A by default; we don't expose it through the
        # positional-arg builder because the codec ignores extra args in
        # the EDG command. This is spec-compliant (the field is present
        # with a legal value) but not spec-operator-controllable. To
        # inject a different EVTargetCurrent you'd need to patch OpenV2G
        # or pre-encode the frame offline.
        self._intercept_and_send(
            "PreChargeReq",
            {
                "SessionID": self.sessionId,
                "SoC": self._soc_str(),
                "EVTargetVoltage": str(int(self.hardwareInterface.getAccuVoltage())),
                # Expose EVTargetCurrent as an override key for future
                # codec upgrades, even though the current binary ignores
                # it. PauseController.set_override("PreChargeReq",
                # {"EVTargetCurrent": N}) would become effective the day
                # we ship an enhanced OpenV2G.
                "EVTargetCurrent": "1",
            },
            lambda p: (
                f"EDG_{p['SessionID']}_{p['SoC']}_{p['EVTargetVoltage']}"
            ),
        )

    def _send_power_delivery_req(self, on: bool) -> None:
        self._intercept_and_send(
            "PowerDeliveryReq",
            {
                "SessionID": self.sessionId,
                "SoC": self._soc_str(),
                "ReadyToChargeState": 1 if on else 0,
            },
            lambda p: (
                f"EDH_{p['SessionID']}_{p['SoC']}_{p['ReadyToChargeState']}"
            ),
        )

    def _send_current_demand_req(self) -> None:
        self._intercept_and_send(
            "CurrentDemandReq",
            {
                "SessionID": self.sessionId,
                "SoC": self._soc_str(),
                "EVTargetCurrent": str(int(self.hardwareInterface.getAccuMaxCurrent())),
                "EVTargetVoltage": str(int(self.hardwareInterface.getAccuMaxVoltage())),
            },
            lambda p: (
                f"EDI_{p['SessionID']}_{p['SoC']}_"
                f"{p['EVTargetCurrent']}_{p['EVTargetVoltage']}"
            ),
        )

    def _send_welding_detection_req(self) -> None:
        self._intercept_and_send(
            "WeldingDetectionReq",
            {"SessionID": self.sessionId, "SoC": self._soc_str()},
            lambda p: f"EDJ_{p['SessionID']}_{p['SoC']}",
        )

    def _send_session_stop_req(self) -> None:
        self._intercept_and_send(
            "SessionStopReq",
            {"SessionID": self.sessionId},
            lambda p: f"EDK_{p['SessionID']}",
        )

    # ---- state handlers -------------------------------------------

    def _state_not_initialized(self) -> None:
        pass

    def _state_connecting(self) -> None:
        if self.cyclesInState < 30:
            return
        evse_ip = self.addressManager.getSeccIp()
        secc_port = self.addressManager.getSeccTcpPort()
        self.addToTrace(f"Checkpoint301: connecting to [{evse_ip}]:{secc_port}")
        self.Tcp.connect(evse_ip, secc_port)
        if not self.Tcp.isConnected:
            self.addToTrace("Connection failed. Retrying.")
            self.reInit()
            return
        self.addToTrace("connected")
        self.publishStatus("TCP connected")
        self.isUserStopRequest = False
        self.enterState(STATE_CONNECTED)

    def _state_connected(self) -> None:
        # supportedAppProtocolReq is a pre-captured EXI blob (OpenV2G can't
        # synthesise one from command-line args). We still route it through
        # the PauseController so playbooks + the GUI can:
        #   (a) pause + observe the outbound blob before transmission, and
        #   (b) swap in a different preset or a hand-crafted hex payload
        #       via ``PauseController.set_override("supportedAppProtocolReq",
        #       {"Preset": "tesla"} | {"PayloadHex": "8000..."})``.
        presets = {
            "ioniq": EXI_DEMO_SUPPORTED_APP_PROTOCOL_IONIQ,
            "tesla": EXI_DEMO_SUPPORTED_APP_PROTOCOL_TESLA,
        }
        defaults = {
            "Preset": self.preferred_protocol,
            "PayloadHex": "",
        }
        params = self.pause_controller.intercept(
            "supportedAppProtocolReq", defaults
        )

        # Explicit PayloadHex wins over preset.
        blob_hex = params.get("PayloadHex") or ""
        if not blob_hex:
            preset = str(params.get("Preset", "ioniq")).lower()
            if preset in ("iso", "both", "generated"):
                # The "generated" preset tries to produce a multi-protocol
                # blob via an enhanced OpenV2G; bundled codec returns None.
                blob_hex = _iso_capable_app_protocol_hex() or ""
                if not blob_hex:
                    self.addToTrace(
                        "[warn] codec lacks EH_ custom-params support; "
                        "falling back to Ioniq DIN-only blob"
                    )
                    blob_hex = EXI_DEMO_SUPPORTED_APP_PROTOCOL_IONIQ
            else:
                blob_hex = presets.get(preset,
                                       EXI_DEMO_SUPPORTED_APP_PROTOCOL_IONIQ)

        self.addToTrace(
            f"Checkpoint400: Sending supportedAppProtocolReq ({blob_hex[:32]}...)"
        )
        data = exiHexToByteArray(blob_hex)

        # Observer hook — match the pattern used by _intercept_and_send for
        # other Req messages, so session logs and the GUI tree view see
        # this as a proper 'tx' event with a decoded view and raw bytes.
        if self.message_observer is not None:
            try:
                decoded_tx = exiDecode(data, "Dh")
                obs_params: dict[str, Any] = {}
                try:
                    obs_params = _json.loads(decoded_tx)
                except (ValueError, TypeError):
                    pass
                obs_params["_raw_exi_hex"] = blob_hex
                name = obs_params.get("msgName", "supportedAppProtocolReq")
                self._notify("tx", name, obs_params)
            except Exception as e:                              # noqa: BLE001
                self.addToTrace(f"[observer decode error] {e}")

        self.Tcp.transmit(bytes(addV2GTPHeader(data)))
        self.enterState(STATE_WAIT_APP_RES)

    def _state_wait_app_res(self) -> None:
        if self.rxData:
            self.addToTrace(
                "WaitAppRes received " + prettyHexMessage(self.rxData)
            )
            # Strip non-EXI testsuite notifications if present.
            if self.rxData[:2] != b"\x01\xfe":
                self.addToTrace("Non-V2GTP prefix; skipping testsuite frame")
                self.rxData = self.rxData[20:] if len(self.rxData) > 20 else b""
                if not self.rxData:
                    return
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "Dh")
            self.addToTrace(decoded)
            if "supportedAppProtocolRes" in decoded:
                self.publishStatus("Schema negotiated")
                self._intercept_and_send(
                    "SessionSetupReq",
                    {"EVCCID": self.evccid},
                    lambda p: f"EDA_{p['EVCCID']}",
                )
                self.enterState(STATE_WAIT_SESSION_SETUP_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_session_setup_res(self) -> None:
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "SessionSetupRes" in decoded:
                try:
                    d = json.loads(decoded)
                    self.sessionId = d.get("header.SessionID", self.sessionId)
                    rc = d.get("ResponseCode", "")
                    self.addToTrace(f"Checkpoint506: SessionId = {self.sessionId}")
                    if rc not in ("OK_NewSessionEstablished", "OK"):
                        self.addToTrace(f"Bad ResponseCode {rc}; aborting")
                        self.enterState(STATE_UNRECOVERABLE_ERROR)
                        return
                except (json.JSONDecodeError, AttributeError) as e:
                    self.addToTrace(f"Could not decode SessionSetupRes: {e}")
                self.publishStatus("Session established")
                self._intercept_and_send(
                    "ServiceDiscoveryReq",
                    {"SessionID": self.sessionId},
                    lambda p: f"EDB_{p['SessionID']}",
                )
                self.enterState(STATE_WAIT_SERVICE_DISCOVERY_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_service_discovery_res(self) -> None:
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "ServiceDiscoveryRes" in decoded:
                self.publishStatus("ServDisc done")
                self._intercept_and_send(
                    "ServicePaymentSelectionReq",
                    {"SessionID": self.sessionId},
                    lambda p: f"EDC_{p['SessionID']}",
                )
                self.enterState(STATE_WAIT_SERVICE_PAYMENT_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_service_payment_res(self) -> None:
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "ServicePaymentSelectionRes" in decoded:
                self.publishStatus("ServPaySel done")
                self._send_contract_auth_req()
                self.numberOfContractAuthenticationReq = 1
                self.enterState(STATE_WAIT_CONTRACT_AUTH_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_contract_auth_res(self) -> None:
        if self.cyclesInState < 30:
            return
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "ContractAuthenticationRes" in decoded:
                if '"EVSEProcessing": "Finished"' in decoded:
                    self.publishStatus("Auth finished")
                    self._send_charge_parameter_discovery_req()
                    self.numberOfChargeParameterDiscoveryReq = 1
                    self.enterState(STATE_WAIT_CHARGE_PARAM_RES)
                else:
                    if self.numberOfContractAuthenticationReq >= 120:
                        self.enterState(STATE_SEQUENCE_TIMEOUT)
                        return
                    self.numberOfContractAuthenticationReq += 1
                    self.publishStatus("Waiting for Auth")
                    self._send_contract_auth_req()
                    self.enterState(STATE_WAIT_CONTRACT_AUTH_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_charge_param_res(self) -> None:
        if self.cyclesInState < 30:
            return
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "ChargeParameterDiscoveryRes" in decoded:
                try:
                    d = json.loads(decoded)
                    rc = d.get("ResponseCode", "")
                    ep = d.get("EVSEProcessing", "")
                except json.JSONDecodeError:
                    rc, ep = "", ""
                if rc and rc != "OK":
                    self.enterState(STATE_UNRECOVERABLE_ERROR)
                    return
                if ep == "Finished":
                    self.publishStatus("ChargeParams discovered")
                    self.hardwareInterface.setStateC()
                    self.hardwareInterface.triggerConnectorLocking()
                    self.enterState(STATE_WAIT_CONNECTOR_LOCK)
                    return
                if self.numberOfChargeParameterDiscoveryReq >= 60:
                    self.enterState(STATE_SEQUENCE_TIMEOUT)
                    return
                self.numberOfChargeParameterDiscoveryReq += 1
                self.publishStatus("disc ChargeParams")
                self._send_charge_parameter_discovery_req()
                self.enterState(STATE_WAIT_CHARGE_PARAM_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_connector_lock(self) -> None:
        if self.hardwareInterface.isConnectorLocked():
            self._send_cable_check_req()
            self.numberOfCableCheckReq = 1
            self.enterState(STATE_WAIT_CABLE_CHECK_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_cable_check_res(self) -> None:
        if self.cyclesInState < 30:
            return
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "CableCheckRes" in decoded:
                try:
                    d = json.loads(decoded)
                    rc = d.get("ResponseCode", "")
                    ep = d.get("EVSEProcessing", "")
                except json.JSONDecodeError:
                    rc, ep = "", ""
                if rc and rc != "OK":
                    self.enterState(STATE_UNRECOVERABLE_ERROR)
                    return
                if ep == "Finished" and rc == "OK":
                    self.publishStatus("CbleChck done")
                    self._send_precharge_req()
                    self.connMgr.ApplOk(31)
                    self.enterState(STATE_WAIT_PRECHARGE_RES)
                    return
                if self.numberOfCableCheckReq > 60:
                    self.enterState(STATE_SEQUENCE_TIMEOUT)
                    return
                self.numberOfCableCheckReq += 1
                self.publishStatus(
                    "CbleChck ongoing",
                    format(self.hardwareInterface.getInletVoltage(), ".0f") + "V",
                )
                self._send_cable_check_req()
                self.enterState(STATE_WAIT_CABLE_CHECK_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_precharge_res(self) -> None:
        self.hardwareInterface.simulatePreCharge()
        if self.DelayCycles > 0:
            self.DelayCycles -= 1
            return
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "PreChargeRes" in decoded:
                u = 0.0
                status_code = "0"
                rc = "na"
                try:
                    d = json.loads(decoded)
                    rc = d.get("ResponseCode", "na")
                    v = d.get("EVSEPresentVoltage.Value", "0")
                    m = d.get("EVSEPresentVoltage.Multiplier", "0")
                    u = float(v) * (10 ** int(m))
                    self.callbackShowStatus(
                        format(u, ".1f"), "EVSEPresentVoltage", "", ""
                    )
                    status_code = d.get("DC_EVSEStatus.EVSEStatusCode", "0")
                except (json.JSONDecodeError, TypeError, ValueError):
                    self.addToTrace("Could not decode PreChargeRes")
                if rc != "OK":
                    self.enterState(STATE_UNRECOVERABLE_ERROR)
                    return
                if is_error_evse_status_code(status_code):
                    self.enterState(STATE_UNRECOVERABLE_ERROR)
                    return
                if not getConfigValueBool("use_evsepresentvoltage_for_precharge_end"):
                    u = self.hardwareInterface.getInletVoltage()
                accu_v = self.hardwareInterface.getAccuVoltage()
                try:
                    u_delta = float(getConfigValue("u_delta_max_for_end_of_precharge"))
                except (ValueError, SystemExit):
                    u_delta = 10.0
                if abs(u - accu_v) < u_delta:
                    self.publishStatus("PreCharge done")
                    if not self.isLightBulbDemo:
                        self.hardwareInterface.setPowerRelayOn()
                    self.DelayCycles = 10
                    self.enterState(STATE_WAIT_CONTACTORS_CLOSED)
                else:
                    self.publishStatus("PreChrge ongoing", format(u, ".0f") + "V")
                    self._send_precharge_req()
                    self.DelayCycles = 15
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_contactors_closed(self) -> None:
        if self.DelayCycles > 0:
            self.DelayCycles -= 1
            return
        if self.isLightBulbDemo:
            ready = True
        else:
            ready = self.hardwareInterface.getPowerRelayConfirmation()
            if ready:
                self.publishStatus("Contactors ON")
        if ready:
            self._send_power_delivery_req(on=True)
            self.wasPowerDeliveryRequestedOn = True
            self.enterState(STATE_WAIT_POWER_DELIVERY_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_power_delivery_res(self) -> None:
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "PowerDeliveryRes" in decoded:
                try:
                    d = json.loads(decoded)
                    rc = d.get("ResponseCode", "na")
                except json.JSONDecodeError:
                    rc = "na"
                if rc != "OK":
                    self.enterState(STATE_UNRECOVERABLE_ERROR)
                    return
                if self.wasPowerDeliveryRequestedOn:
                    self.publishStatus("PwrDelvy ON success")
                    self._send_current_demand_req()
                    self.enterState(STATE_WAIT_CURRENT_DEMAND_RES)
                else:
                    self.publishStatus("PwrDelvy OFF success")
                    self.hardwareInterface.setStateB()
                    self.hardwareInterface.setPowerRelayOff()
                    self.hardwareInterface.setRelay2Off()
                    self.isBulbOn = False
                    self._send_welding_detection_req()
                    self.enterState(STATE_WAIT_WELDING_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_current_demand_res(self) -> None:
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "CurrentDemandRes" in decoded:
                u = 0.0
                i = 0.0
                status_code = "0"
                rc = "na"
                try:
                    d = json.loads(decoded)
                    rc = d.get("ResponseCode", "na")
                    v = d.get("EVSEPresentVoltage.Value", "0")
                    vm = d.get("EVSEPresentVoltage.Multiplier", "0")
                    u = float(v) * (10 ** int(vm))
                    c = d.get("EVSEPresentCurrent.Value", "0")
                    cm = d.get("EVSEPresentCurrent.Multiplier", "0")
                    i = float(c) * (10 ** int(cm))
                    self.callbackShowStatus(
                        format(u, ".1f"), "EVSEPresentVoltage", "", ""
                    )
                    status_code = d.get("DC_EVSEStatus.EVSEStatusCode", "0")
                    self.hardwareInterface.setChargerVoltageAndCurrent(u, i)
                except (json.JSONDecodeError, TypeError, ValueError):
                    self.addToTrace("Could not decode CurrentDemandRes")
                if rc != "OK":
                    self.enterState(STATE_UNRECOVERABLE_ERROR)
                    return
                if is_error_evse_status_code(status_code):
                    self.enterState(STATE_UNRECOVERABLE_ERROR)
                    return
                if self.hardwareInterface.getIsAccuFull() or self.isUserStopRequest:
                    reason = (
                        "Accu full"
                        if self.hardwareInterface.getIsAccuFull()
                        else "User req stop"
                    )
                    self.publishStatus(reason)
                    self._send_power_delivery_req(on=False)
                    self.wasPowerDeliveryRequestedOn = False
                    self.enterState(STATE_WAIT_POWER_DELIVERY_RES)
                else:
                    self.publishStatus(
                        "Charging",
                        format(u, ".0f") + "V",
                        format(self.hardwareInterface.getSoc(), ".1f") + "%",
                    )
                    self._send_current_demand_req()
                    self.enterState(STATE_WAIT_CURRENT_DEMAND_RES)
        if self.isLightBulbDemo:
            if self.cyclesLightBulbDelay <= 33 * 2:
                self.cyclesLightBulbDelay += 1
            elif not self.isBulbOn:
                self.addToTrace("Light-bulb demo: turning bulb on")
                self.hardwareInterface.setPowerRelayOn()
                self.hardwareInterface.setRelay2On()
                self.isBulbOn = True
        if self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_welding_res(self) -> None:
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "WeldingDetectionRes" in decoded:
                self.publishStatus("WldingDet done")
                self._send_session_stop_req()
                self.enterState(STATE_WAIT_SESSION_STOP_RES)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_wait_session_stop_res(self) -> None:
        if self.rxData:
            exi = removeV2GTPHeader(self.rxData)
            self.rxData = b""
            decoded = self._decode(exi, "DD")
            self.addToTrace(decoded)
            if "SessionStopRes" in decoded:
                self.publishStatus("Stopped normally")
                self.enterState(STATE_CHARGING_FINISHED)
        elif self.isTooLong():
            self.enterState(STATE_SEQUENCE_TIMEOUT)

    def _state_charging_finished(self) -> None:
        self.addToTrace("Charging successfully finished — unlocking connector")
        self.hardwareInterface.triggerConnectorUnlocking()
        self.enterState(STATE_END)

    def _state_sequence_timeout(self) -> None:
        self.publishStatus("ERROR Timeout")
        self.addToTrace("Safe-shutdown-sequence: setting CP to state B")
        self.hardwareInterface.setStateB()
        self.DelayCycles = 66
        self.enterState(STATE_SAFE_SHUTDOWN_WAIT_CHARGER)

    def _state_unrecoverable_error(self) -> None:
        self.publishStatus("ERROR reported")
        self.addToTrace("Safe-shutdown-sequence: setting CP to state B")
        self.hardwareInterface.setStateB()
        self.DelayCycles = 66
        self.enterState(STATE_SAFE_SHUTDOWN_WAIT_CHARGER)

    def _state_safe_shutdown_wait_charger(self) -> None:
        self.connMgr.ApplOk()
        if self.DelayCycles > 0:
            self.DelayCycles -= 1
            return
        self.addToTrace("Safe-shutdown-sequence: opening contactors")
        self.hardwareInterface.setPowerRelayOff()
        self.hardwareInterface.setRelay2Off()
        self.DelayCycles = 33
        self.enterState(STATE_SAFE_SHUTDOWN_WAIT_CONTACTORS)

    def _state_safe_shutdown_wait_contactors(self) -> None:
        self.connMgr.ApplOk()
        if self.DelayCycles > 0:
            self.DelayCycles -= 1
            return
        self.addToTrace("Safe-shutdown-sequence: unlocking connector")
        self.hardwareInterface.triggerConnectorUnlocking()
        self.enterState(STATE_END)

    def _state_end(self) -> None:
        pass

    # ---- dispatch --------------------------------------------------

    _dispatch = {
        STATE_NOT_INITIALIZED: _state_not_initialized,
        STATE_CONNECTING: _state_connecting,
        STATE_CONNECTED: _state_connected,
        STATE_WAIT_APP_RES: _state_wait_app_res,
        STATE_WAIT_SESSION_SETUP_RES: _state_wait_session_setup_res,
        STATE_WAIT_SERVICE_DISCOVERY_RES: _state_wait_service_discovery_res,
        STATE_WAIT_SERVICE_PAYMENT_RES: _state_wait_service_payment_res,
        STATE_WAIT_CONTRACT_AUTH_RES: _state_wait_contract_auth_res,
        STATE_WAIT_CHARGE_PARAM_RES: _state_wait_charge_param_res,
        STATE_WAIT_CONNECTOR_LOCK: _state_wait_connector_lock,
        STATE_WAIT_CABLE_CHECK_RES: _state_wait_cable_check_res,
        STATE_WAIT_PRECHARGE_RES: _state_wait_precharge_res,
        STATE_WAIT_CONTACTORS_CLOSED: _state_wait_contactors_closed,
        STATE_WAIT_POWER_DELIVERY_RES: _state_wait_power_delivery_res,
        STATE_WAIT_CURRENT_DEMAND_RES: _state_wait_current_demand_res,
        STATE_WAIT_WELDING_RES: _state_wait_welding_res,
        STATE_WAIT_SESSION_STOP_RES: _state_wait_session_stop_res,
        STATE_CHARGING_FINISHED: _state_charging_finished,
        STATE_SEQUENCE_TIMEOUT: _state_sequence_timeout,
        STATE_UNRECOVERABLE_ERROR: _state_unrecoverable_error,
        STATE_SAFE_SHUTDOWN_WAIT_CHARGER: _state_safe_shutdown_wait_charger,
        STATE_SAFE_SHUTDOWN_WAIT_CONTACTORS: _state_safe_shutdown_wait_contactors,
        STATE_END: _state_end,
    }

    def mainfunction(self) -> None:
        if self.Tcp.isRxDataAvailable():
            self.rxData = self.Tcp.getRxData()
        self.cyclesInState += 1
        handler = self._dispatch.get(self.state)
        if handler is None:
            self.addToTrace(f"Unknown state {self.state}; resetting")
            self.reInit()
            return
        handler(self)
