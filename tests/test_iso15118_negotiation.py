"""Tests for A1 — ISO 15118-2 schema negotiation on the EVSE side.

We drive the EVSE FSM's app-handshake handler directly with synthetic
decoded-JSON strings representing what the EV offered. The FSM should
end up in ``schemaSelection == "1"`` when ISO is chosen and
``"D"`` when DIN wins.

These tests don't exercise the TCP or EXI paths — they poke the
state machine's internal dispatch so we can verify the negotiation
logic without running a full session.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini")
)


def _stub_fsm(protocol_preference: str = "prefer_din"):
    """Construct a minimal fsmEvse suitable for app-handshake probing."""
    from hotwire.fsm import fsm_evse
    from hotwire.fsm.pause_controller import PauseController

    f = fsm_evse.fsmEvse.__new__(fsm_evse.fsmEvse)
    f.callbackAddToTrace = lambda s: None
    f.callbackShowStatus = lambda *a, **kw: None
    f.pause_controller = PauseController()
    f.message_observer = None
    f.state = fsm_evse.STATE_WAIT_APP_HANDSHAKE
    f.cyclesInState = 0
    f.rxData = b""
    f.schemaSelection = "D"
    f.blChargeStopTrigger = False
    f.evccid = ""

    class _FakeTcp:
        def transmit(self, msg):
            return 0
    f.Tcp = _FakeTcp()

    # Monkeypatch config.getConfigValue to return the requested preference
    # for just this test.
    import hotwire.core.config as cfg
    orig = cfg.getConfigValue

    def fake_get(key: str) -> str:
        if key == "protocol_preference":
            return protocol_preference
        return orig(key)

    cfg.getConfigValue = fake_get
    return f, (cfg, orig)


def _restore(state):
    cfg, orig = state
    cfg.getConfigValue = orig


def test_din_only_offer_selects_din():
    f, state = _stub_fsm("prefer_din")
    try:
        # Synthesize rxData that decodes to a DIN-only supportedAppProtocolReq.
        # We fake the decode path by monkeypatching exiDecode at the module
        # level.
        import hotwire.fsm.fsm_evse as mod

        fake_decoded = (
            '{"msgName": "supportedAppProtocolReq",'
            '"AppProtocol_arrayLen": "1",'
            '"NameSpace_0": "urn:din:70121:2012:MsgDef",'
            '"SchemaID_0": "1"}'
        )
        # Preload rxData with V2GTP-wrapped fake bytes so the decode path runs.
        f.rxData = b"\x01\xfe\x80\x01\x00\x00\x00\x01\x00"

        orig_decode = mod.exiDecode
        mod.exiDecode = lambda *a, **kw: fake_decoded

        # Also swap the tx-side encode + observer so _intercept_and_send doesn't
        # blow up trying to call a real codec.
        orig_encode = mod.exiEncode
        mod.exiEncode = lambda cmd: "deadbeef"
        orig_v2g = mod.addV2GTPHeader
        mod.addV2GTPHeader = lambda b: bytearray(b"\x00\x00\x00\x00") + (
            bytearray.fromhex(b) if isinstance(b, str) else bytearray(b)
        )
        try:
            mod.fsmEvse._state_wait_app_handshake(f)
        finally:
            mod.exiDecode = orig_decode
            mod.exiEncode = orig_encode
            mod.addV2GTPHeader = orig_v2g

        assert f.schemaSelection == "D"
    finally:
        _restore(state)


def test_iso_only_offer_selects_iso_under_prefer_iso():
    f, state = _stub_fsm("prefer_iso")
    try:
        import hotwire.fsm.fsm_evse as mod
        fake_decoded = (
            '{"msgName": "supportedAppProtocolReq",'
            '"AppProtocol_arrayLen": "1",'
            '"NameSpace_0": "urn:iso:15118:2:2013:MsgDef",'
            '"SchemaID_0": "2"}'
        )
        f.rxData = b"\x01\xfe\x80\x01\x00\x00\x00\x01\x00"
        orig_decode = mod.exiDecode
        mod.exiDecode = lambda *a, **kw: fake_decoded
        orig_encode = mod.exiEncode
        mod.exiEncode = lambda cmd: "deadbeef"
        orig_v2g = mod.addV2GTPHeader
        mod.addV2GTPHeader = lambda b: bytearray(b"\x00\x00\x00\x00") + (
            bytearray.fromhex(b) if isinstance(b, str) else bytearray(b)
        )
        try:
            mod.fsmEvse._state_wait_app_handshake(f)
        finally:
            mod.exiDecode = orig_decode
            mod.exiEncode = orig_encode
            mod.addV2GTPHeader = orig_v2g

        assert f.schemaSelection == "1"
    finally:
        _restore(state)


def test_both_offered_under_prefer_iso_picks_iso():
    f, state = _stub_fsm("prefer_iso")
    try:
        import hotwire.fsm.fsm_evse as mod
        fake_decoded = (
            '{"msgName": "supportedAppProtocolReq",'
            '"AppProtocol_arrayLen": "2",'
            '"NameSpace_0": "urn:din:70121:2012:MsgDef",'
            '"SchemaID_0": "1",'
            '"NameSpace_1": "urn:iso:15118:2:2013:MsgDef",'
            '"SchemaID_1": "2"}'
        )
        f.rxData = b"\x01\xfe\x80\x01\x00\x00\x00\x01\x00"
        orig_decode = mod.exiDecode
        mod.exiDecode = lambda *a, **kw: fake_decoded
        orig_encode = mod.exiEncode
        mod.exiEncode = lambda cmd: "deadbeef"
        orig_v2g = mod.addV2GTPHeader
        mod.addV2GTPHeader = lambda b: bytearray(b"\x00\x00\x00\x00") + (
            bytearray.fromhex(b) if isinstance(b, str) else bytearray(b)
        )
        try:
            mod.fsmEvse._state_wait_app_handshake(f)
        finally:
            mod.exiDecode = orig_decode
            mod.exiEncode = orig_encode
            mod.addV2GTPHeader = orig_v2g
        assert f.schemaSelection == "1"
    finally:
        _restore(state)


def test_both_offered_under_prefer_din_picks_din():
    f, state = _stub_fsm("prefer_din")
    try:
        import hotwire.fsm.fsm_evse as mod
        fake_decoded = (
            '{"msgName": "supportedAppProtocolReq",'
            '"AppProtocol_arrayLen": "2",'
            '"NameSpace_0": "urn:din:70121:2012:MsgDef",'
            '"SchemaID_0": "1",'
            '"NameSpace_1": "urn:iso:15118:2:2013:MsgDef",'
            '"SchemaID_1": "2"}'
        )
        f.rxData = b"\x01\xfe\x80\x01\x00\x00\x00\x01\x00"
        orig_decode = mod.exiDecode
        mod.exiDecode = lambda *a, **kw: fake_decoded
        orig_encode = mod.exiEncode
        mod.exiEncode = lambda cmd: "deadbeef"
        orig_v2g = mod.addV2GTPHeader
        mod.addV2GTPHeader = lambda b: bytearray(b"\x00\x00\x00\x00") + (
            bytearray.fromhex(b) if isinstance(b, str) else bytearray(b)
        )
        try:
            mod.fsmEvse._state_wait_app_handshake(f)
        finally:
            mod.exiDecode = orig_decode
            mod.exiEncode = orig_encode
            mod.addV2GTPHeader = orig_v2g
        assert f.schemaSelection == "D"
    finally:
        _restore(state)
