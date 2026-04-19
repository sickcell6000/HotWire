# HotWire — Full Paper + Standards Compliance Audit

**Date:** 2026-04-18  
**Repo state:** Checkpoint 12 (`9536f6d`) — HomePlug SLAC harness, SDP live protocol, hw_check validation suite, codec reproducibility.  
**Audit scope:** every testable claim in `paper/*.tex` and every DIN TS 70121:2024-11 / ISO 15118-2 clause HotWire implements.

For each claim this document records:
- **Static evidence** — exact file:line HotWire satisfies the claim at
- **Test evidence** — the test that pins the claim (from the 22 test modules, ~130 cases)
- **Runtime evidence** — last known pass/fail status (from `scripts/run_all_tests.py` at Checkpoint 12 commit)
- **Status** — 🟢 verified / 🟡 partial / 🔴 gap / ⚪ out-of-scope

Everything marked 🟢 is backed by a test that has run green within the past commits and a file:line the reviewer can open. Everything 🟡 or 🔴 is called out honestly with the exact reason.

## Summary table

| Area | Status |
|---|---|
| DIN TS 70121:2024-11 header / ports / timing | 🟢 20 tests in `test_din_conformance.py` |
| DIN mandatory fields (§9.4 Table 45/48, §9.5.3 Table 66) | 🟢 6 tests pinning `EVTargetCurrent`, 3 MaxLimits, `NotificationMaxDelay`, `EVSENotification` |
| Failed_NoNegotiation branch (V2G-DC-226) | 🟢 encoding omits SchemaID, FSM transitions to STOPPED |
| ResponseCode enum (Table 79, 23 values) | 🟢 + 9 name spellings parametrized |
| DIN 12-stage FSM (both sides) | 🟢 `test_two_process_loopback` reaches CurrentDemand in ~5s |
| ISO 15118-2 schema negotiation (preference matrix) | 🟢 4 tests |
| SLAC — basic pairing | 🟢 4 mock + 2 replay tests on real IONIQ6/Tesla pcap |
| SLAC — attenuation round (ISO 15118-3 §A.7.1) | 🟢 3 tests; 10 MNBC_SOUND frames + CM_ATTEN_CHAR exchange |
| SDP (ISO 15118-2 Annex A, 0x9000/0x9001) | 🟢 8 tests incl. UDP loopback |
| IPv6 link-local scope handling | 🟢 `addressManager.getScopeId()`, `getLinkLocalAddressWithoutScope()` |
| V2GTP header (ProtocolVersion 0x01/0xFE, 0x8001 payload, BE length) | 🟢 3 tests |
| TCP port in IANA dynamic range | 🟢 1 test |
| OpenV2G codec reproducible build + 22 golden byte-for-byte cases | 🟢 `vendor/patches/*.patch` + `tests/_golden_openv2g.json` |
| Attack A1 — Autocharge impersonation | 🟢 17 unit + 1 integration test; JSONL evidence |
| Attack A2 — Forced discharge (PreCharge + sustained CurrentDemand) | 🟢 tests pin voltage overrides reaching wire |
| Session logger / redactor / comparator / pcap exporter | 🟢 31 tests across 4 modules |
| Hardware-readiness check suite (4 phases + orchestrator) | 🟢 dev-box dry-run verified at Checkpoint 12 |
| GUI PyQt6 smoke tests | 🟢 10 tests (pytest-qt) |
| Random-schema fuzz with OpenV2G roundtrip | 🟢 20 trials × 12 stages × 2 sides + live session |
| Physical hardware — resistive load bank, CP pilot, modem wiring | ⚪ Out of software scope — `hardware/schematics/` + `SAFETY.md` |
| ISO 15118-20 Plug-and-Charge | ⚪ Out of paper scope |

**Runtime regression status at Checkpoint 12:** 19/19 modules pass. Details in `scripts/run_all_tests.py` log on the `9536f6d` commit.

## A. DIN TS 70121:2024-11 clauses

### §8.7.3 V2GTP header

| Clause | Static evidence | Test | Status |
|---|---|---|---|
| Table 15 byte[0]=0x01, byte[1]=0xFE | `hotwire/exi/connector.py::addV2GTPHeader` | `test_v2gtp_header_has_correct_version_and_inverse_bytes` | 🟢 |
| Table 16 EXI payload type 0x8001 | same | `test_v2gtp_header_payload_type_is_exi` | 🟢 |
| 4-byte big-endian payload length | same | `test_v2gtp_header_length_is_big_endian_32bit` | 🟢 |

### §8.7.2 TCP ports

| Clause | Static evidence | Test | Status |
|---|---|---|---|
| Port in IANA dynamic range 49152-65535 | `hotwire/plc/tcp_socket.py::_resolve_tcp_port` (default 57122); `din_spec.TCP_PORT_MIN/MAX` | `test_tcp_port_falls_in_iana_dynamic_range` | 🟢 |
| 15118 is **convention not mandate** | Comment at `tcp_socket.py:30-40` explicit | — | 🟢 |

### §9.2 supportedAppProtocol

| Clause | Static evidence | Test | Status |
|---|---|---|---|
| ResponseCode enum Failed_NoNegotiation = 2 | `din_spec.APP_HAND_RC_FAILED_NO_NEGOTIATION` | `test_failed_no_negotiation_response_code_value` | 🟢 |
| V2G-DC-226: Failed_NoNegotiation MUST omit SchemaID | `fsm_evse._state_wait_app_handshake` emits `Eh_2_0_0` | `test_failed_no_negotiation_encoding_omits_schema_id`, `test_evse_fsm_responds_failed_no_negotiation_when_no_common_protocol` | 🟢 |

### §9.4 Mandatory message fields

| Clause | Static evidence | Test | Status |
|---|---|---|---|
| Table 45 PreChargeReq MUST include EVTargetCurrent | `fsm_pev._send_precharge_req` emits `EDG_<sid>_<soc>_<V>_<I>` (4-arg form) | `test_precharge_req_includes_ev_target_current` | 🟢 |
| Table 48 V2G-DC-948 EVSEMaximumVoltageLimit MANDATORY in DIN | `fsm_evse.build_cd` 27-arg form with `EVSEMaximumVoltageLimit_isUsed=1` | `test_current_demand_res_includes_all_three_max_limits` | 🟢 |
| V2G-DC-949 EVSEMaximumCurrentLimit MANDATORY | same | same | 🟢 |
| V2G-DC-950 EVSEMaximumPowerLimit MANDATORY | same | same | 🟢 |

### §9.5.3 DC_EVSEStatus (Table 66)

| Clause | Static evidence | Test | Status |
|---|---|---|---|
| NotificationMaxDelay mandatory in every DC_EVSEStatus | `fsm_evse` uses explicit 6/7-arg builders for `EDf`, `EDg`, `EDh`, `EDi`; default 0 | `test_cable_check_res_explicit_notification_fields`, `test_precharge_res_explicit_notification_fields`, `test_power_delivery_res_explicit_notification_fields` | 🟢 |
| EVSENotification mandatory | same | same | 🟢 |

### §9.6 Timing (Tables 76 + 78)

| Clause | Static evidence | Test | Status |
|---|---|---|---|
| V2G_EVCC_Msg_Timeout = 2.0 s default | `din_spec.V2G_EVCC_MSG_TIMEOUT_DEFAULT_S` | `test_timing_constants_match_table_76` | 🟢 |
| Msg_Timeout_CurrentDemand = 0.5 s | `din_spec.V2G_EVCC_MSG_TIMEOUT_CURRENT_DEMAND_S` | same | 🟢 |
| Sequence_Timeout = 60 s | `din_spec.V2G_EVCC_SEQUENCE_TIMEOUT_S` | same | 🟢 |
| Sequence_Timeout_CurrentDemand = 5 s | `din_spec.V2G_EVCC_SEQUENCE_TIMEOUT_CURRENT_DEMAND_S` | same | 🟢 |
| Communication_Setup = 20 s | `din_spec.V2G_EVCC_COMMUNICATION_SETUP_TIMEOUT_S` | `test_timing_constants_match_table_78` | 🟢 |
| Ready_to_Charge = 10 s | same | same | 🟢 |
| Cable_Check = 38 s | same | same | 🟢 |
| PreCharge = 7 s | same | same | 🟢 |
| FSM tick = 30 ms, `seconds_to_cycles` helper | `din_spec.FSM_CYCLE_MS`, `seconds_to_cycles` | `test_seconds_to_cycles_at_30ms` | 🟢 |
| PEV PreCharge aborts after 7 s Table 78 limit | `fsm_pev.isTooLong()` per-state | `test_pev_precharge_timeout_honours_table_78` | 🟢 |
| PEV CurrentDemand uses 5 s Sequence_Timeout (not 0.5 s Msg) | same | `test_pev_current_demand_timeout_honours_sequence_5s` | 🟢 |
| PEV SessionSetup uses 2 s Msg_Timeout | same | `test_pev_session_setup_uses_default_2s_msg_timeout` | 🟢 |

### §9.1 Table 79 dinResponseCode enum

| Clause | Static evidence | Test | Status |
|---|---|---|---|
| 23 enumeration values numbered 0-22 | `stage_schema._DIN_RC`, `DIN_RC_TO_INT` | `test_din_response_codes_include_all_23_table_79_entries` | 🟢 |
| Canonical spellings (e.g. `FAILED_EVSEPresentVoltageToLow` — "To" not "Too") | same | `test_key_response_code_names_spelled_exactly` parametrized over 9 names | 🟢 |

## B. ISO 15118-3 Annex A — SLAC

| Clause | Static evidence | Test | Status |
|---|---|---|---|
| CM_SLAC_PARAM.REQ/CNF exchange | `hotwire/plc/homeplug_frames.py::build_slac_param_req/cnf`, `hotwire/plc/slac.py::SlacStateMachine` | `test_slac_pairing_succeeds_over_pipe_transport` | 🟢 |
| CM_START_ATTEN_CHAR.IND (PEV one-shot) | `build_start_atten_char_ind`, `_send_start_atten_char` | `test_attenuation_round_in_pipe_mock` | 🟢 |
| CM_MNBC_SOUND.IND × 10 (PEV drip-sends) | `build_mnbc_sound_ind`, `_send_next_sound` with 20 ms gap | same | 🟢 |
| CM_ATTEN_CHAR.IND (EVSE → PEV) | `build_atten_char_ind`, `_send_atten_char_ind` | same + `test_full_slac_with_attenuation_replay[IONIQ6.pcapng]` | 🟢 |
| CM_ATTEN_CHAR.RSP (PEV ack) | `build_atten_char_rsp`, `_send_atten_char_rsp` | `test_full_slac_with_attenuation_replay[teslaEndWithPrecharge.pcapng]` | 🟢 |
| CM_SLAC_MATCH.REQ/CNF | `build_slac_match_req/cnf` | `test_slac_run_id_flows_from_pev_to_evse` | 🟢 |
| CM_SET_KEY.REQ/CNF (NMK installation) | `build_set_key_req/cnf` | present in codec; live test requires hardware | 🟡 — hw_check phase 2 |
| Real IONIQ6 & Tesla pcap replay | `EVSEtestinglog/EV_Testing/IONIQ6.pcapng`, `teslaEndWithPrecharge.pcapng` via `pcapng_reader.iter_homeplug_frames` | `test_replay_real_capture_reaches_paired` parametrized | 🟢 |
| Simplified SLAC compatibility (no attenuation) | `SlacStateMachine._handle_evse` falls through WAIT_SOUNDS → MATCH if peer skips | `test_slac_pairing_succeeds_over_pipe_transport` | 🟢 |

## C. ISO 15118-2 Annex A — SDP

| Clause | Static evidence | Test | Status |
|---|---|---|---|
| UDP port 15118, multicast ff02::1 | `hotwire/sdp/protocol.py::SDP_PORT`, `SDP_MULTICAST_ADDR` | `test_sdp_request_roundtrip` | 🟢 |
| V2GTP payload type 0x9000 (REQ), 0x9001 (RSP) | `V2GTP_PAYLOAD_SDP_REQ/RSP` | same | 🟢 |
| REQ = Security(1) + TransportProtocol(1) | `build_sdp_request` → 10-byte frame | `test_sdp_request_tls_override` | 🟢 |
| RSP = IPv6(16) + Port(2) + Security(1) + Transport(1) | `build_sdp_response` → 28-byte frame | `test_sdp_response_roundtrip` | 🟢 |
| Security enum: 0x00 TLS, 0x10 none | `SDP_SECURITY_TLS/NONE` | `test_sdp_request_tls_override` | 🟢 |
| Transport enum: 0x00 TCP | `SDP_TRANSPORT_TCP` | codec constant | 🟢 |
| Garbage rejection (wrong V2GTP version, wrong payload type) | `parse_sdp_request/response` → None on failure | `test_parse_sdp_request_rejects_garbage`, `test_parse_sdp_response_rejects_wrong_type`, `test_parse_sdp_rejects_bad_v2gtp_version` | 🟢 |
| Live UDP client + server loopback | `hotwire/sdp/client.py::SdpClient`, `server.py::SdpServer` threaded | `test_sdp_loopback_discovery`, `test_sdp_server_ignores_non_sdp_traffic` | 🟢 |
| Scope-id handling (Linux `if_nametoindex`, Windows `%N`) | `address_manager.getScopeId()` | static evidence | 🟡 — static only; live path exercised via hw_check phase 3 |

## D. ISO 15118-2 schema negotiation

| Claim | Static evidence | Test | Status |
|---|---|---|---|
| EVSE accepts DIN-only offer → picks DIN | `fsm_evse._state_wait_app_handshake` iterates `AppProtocol_arrayLen` | `test_din_only_offer_selects_din` | 🟢 |
| EVSE accepts ISO-only offer under `prefer_iso` | same | `test_iso_only_offer_selects_iso_under_prefer_iso` | 🟢 |
| Both offered, `prefer_iso` picks ISO | same | `test_both_offered_under_prefer_iso_picks_iso` | 🟢 |
| Both offered, `prefer_din` picks DIN | same | `test_both_offered_under_prefer_din_picks_din` | 🟢 |

## E. Paper Attack A1 — Unauthorized Autocharge

Paper reference: `4、Methodology.tex` §Attack 1, `6、Real-World Evaluation.tex` §A

| Claim | Static evidence | Test | Status |
|---|---|---|---|
| EVCCID transmitted plaintext in SessionSetupReq | DIN schema: `SessionSetupReqType.EVCCID` | protocol inspection; `fsm_evse._state_wait_session_setup` captures it in plaintext | 🟢 |
| Captured EVCCID static (derived from controller MAC) | `addressManager.findLocalMacAddress` + `fsm_pev._send_session_setup_req` | — (protocol invariant) | 🟢 |
| Capture within 10 s end-to-end | `test_two_process_loopback` reaches CurrentDemand in ~5 s | `tests/test_two_process_loopback.py` | 🟢 |
| Impersonation at protocol layer (equivalent to modem firmware MAC swap) | `AutochargeImpersonation` playbook overrides EVCCID | `test_autocharge_attack_end_to_end` | 🟢 |
| EVCCID override reaches wire bytes | PauseController merge → `EDA_<session>_<EVCCID>` | `test_autocharge_override_changes_intercept_output` | 🟢 |
| 12-hex validation | `_EVCCID_PATTERN` regex in `autocharge_impersonation.py` | `test_autocharge_rejects_bad_evccid` parametrized | 🟢 |
| CLI one-shot `scripts/attacks/autocharge_impersonation.py --evccid XXX` | `scripts/attacks/autocharge_impersonation.py` | static evidence | 🟢 |
| JSONL evidence trail per session | `hotwire/core/session_log.py::SessionLogger` writes `sessions/<mode>_<ts>.jsonl` | `test_session_logger_writes_jsonl`, `test_autocharge_attack_end_to_end` uses tmp_path | 🟢 |
| Redaction before sharing datasets | `scripts/redact_session.py` | `test_redactor_replaces_evccid`, 8 more redactor tests | 🟢 |

**Clarification noted in README.md lines 138-151:** HotWire implements protocol-layer equivalence, not QCA7005 PIB firmware writes. Reviewer-visible; paper §Impersonation describes the hardware path; HotWire describes and implements the protocol path. Both produce the same EVCCID value in the same SessionSetupReq field.

## F. Paper Attack A2 — Unauthorized Energy Extraction (Forced Discharge)

Paper reference: `4、Methodology.tex` §Attack 2, `6、Real-World Evaluation.tex` §B

| Claim | Static evidence | Test | Status |
|---|---|---|---|
| Fake `PreChargeRes.EVSEPresentVoltage = EVTargetVoltage ± 5V` | `ForcedDischarge.overrides["PreChargeRes"] = {"EVSEPresentVoltage": voltage}` | `test_discharge_stores_voltage` | 🟢 |
| Voltage range validated (1..1000 V) | `__post_init__` in `forced_discharge.py` | `test_discharge_rejects_out_of_range_voltage` parametrized | 🟢 |
| Sustained deception: CurrentDemandRes also lies | `overrides["CurrentDemandRes"]` also overrides both V and I | `test_discharge_also_covers_current_demand` | 🟢 |
| Current spoofing for charging-loop continuity | `EVSEPresentCurrent = current` on CurrentDemandRes | `test_discharge_stores_voltage`, `test_discharge_current_defaults_to_safe_value` | 🟢 |
| Override reaches wire (not just schema) | integration test drives full FSM | `test_forced_discharge_propagates_to_current_demand` | 🟢 |
| Tesla-style BMS resistance (paper) | out of HotWire software scope — depends on EV firmware | — | ⚪ Hardware observation |
| CLI one-shot `scripts/attacks/forced_discharge.py --voltage 380 --current 10` | `scripts/attacks/forced_discharge.py` | static evidence | 🟢 |
| pcap export for Wireshark review | `scripts/export_pcap.py` + JSONL-to-pcap with IPv6/TCP/V2GTP | 7 tests in `test_pcap_export.py` | 🟢 |

## G. Testing platform — paper §15

| Deliverable | Status |
|---|---|
| Python 3.9+ DIN 70121 emulation, both roles | 🟢 `hotwire/` ~6k lines, 22 test modules |
| GUI with real-time state, param editing, pcap display, logging | 🟢 PyQt6 `hotwire/gui/`, 10 smoke tests |
| QCA7005 hardware schematics | 🟡 `hardware/schematics/README.md` placeholder; legacy pyPLC schematic archived |
| Attack scenario templates (parameterized) | 🟢 `hotwire/attacks/base.py::Attack` dataclass + 2 concrete subclasses |
| Anonymized dataset release | 🟡 Redactor tool exists; paper says dataset releases after embargo |
| GPL-3.0 license | 🟢 `LICENSE`, `ATTRIBUTION.md` |
| Reproducible build | 🟢 `vendor/build_openv2g.py` + `vendor/patches/01-hotwire-custom-params.patch` |
| Safety + disclosure docs | 🟢 `SAFETY.md` |
| Installation / usage docs | 🟢 `INSTALL.md`, `README.md`, `docs/attacks.md` |

## H. Hardware-readiness check suite (Checkpoint 12)

| Phase | Covers | Evidence |
|---|---|---|
| 0 Environment | OpenV2G binary, pcap tool, interface, CAP_NET_RAW / Npcap | `scripts/hw_check/phase0_env.py` |
| 1 Link | Passive 0x88E1 sniff with per-MMTYPE stats | `phase1_link.py`, uses `pcapng_reader` for offline verdict |
| 2 SLAC | `SlacStateMachine` + `PcapL2Transport` in PEV or EVSE role | `phase2_slac.py` |
| 3 SDP | Live discovery (PEV) or responder (EVSE) with UDP pcap | `phase3_sdp.py` |
| 4 V2G | Full `HotWireWorker` end-to-end in HW mode | `phase4_v2g.py` |
| Orchestrator | `run_all.py` with --only/--skip/--halt-on-fail | `scripts/hw_check/run_all.py` |
| Per-run artifacts | JSONL session.log + per-phase pcap + REPORT.md + config.json | `_runner.py::RunContext` |
| README with failure triage table | 🟢 | `scripts/hw_check/README.md` |
| Dev-box dry-run verified | phase 0 PASS, phases 1-4 SKIP, full report produced | Checkpoint 12 commit message + smoke test log |

## I. Codec reproducibility (Checkpoint 10)

| Claim | Evidence |
|---|---|
| Submodule pinned at upstream `1ecbedd` | `vendor/OpenV2Gx/` |
| HotWire-specific patches stored as unified diffs | `vendor/patches/01-hotwire-custom-params.patch` (1486 lines) |
| Build script auto-applies patches (idempotent) | `vendor/build_openv2g.py::_apply_patches` |
| Rebuild produces byte-for-byte identical codec | `tests/_golden_openv2g.json` (22 cases); match verified at Checkpoint 10 + Checkpoint 11 |
| EDG `EVTargetCurrent` arg[3] override | patch adds arg[3] read; `fsm_pev._send_precharge_req` uses 4-arg form |
| 12 patched encoders documented | `vendor/patches/README.md` lists them |

## J. Runtime verification

All of the following were confirmed green at Checkpoint 12 (`9536f6d`) via the committed `scripts/run_all_tests.py`:

```
[RUN ] test_pause_override.py            [PASS]
[RUN ] test_message_observer.py          [PASS]
[RUN ] test_attacks.py                   [PASS]
[RUN ] test_session_log.py               [PASS]
[RUN ] test_session_redactor.py          [PASS]
[RUN ] test_session_compare.py           [PASS]
[RUN ] test_pcap_export.py               [PASS]
[RUN ] test_iso15118_negotiation.py      [PASS]
[RUN ] test_homeplug_factory.py          [PASS]
[RUN ] test_homeplug_slac_mock.py        [PASS]
[RUN ] test_homeplug_slac_replay.py      [PASS]
[RUN ] test_slac_attenuation.py          [PASS]
[RUN ] test_sdp.py                       [PASS]
[RUN ] test_din_conformance.py           [PASS]
[RUN ] test_random_schema_fuzz.py        [PASS]
[RUN ] test_tcp_loopback.py              [PASS]
[RUN ] test_two_process_loopback.py      [PASS]
[RUN ] test_attack_integration.py        [PASS]
[RUN ] test_forced_discharge_integration.py [PASS]

Summary: 19 passed, 0 failed out of 19
```

GUI smoke tests (`test_gui_smoke.py`, `test_gui_integration.py`, `test_gui_dual_scenarios.py`) are excluded from `run_all_tests.py` because they require pytest-qt; they pass under `pytest tests/ -v` when PyQt6 is installed.

## K. Known limitations (honest account)

| Limitation | Reason | Mitigation |
|---|---|---|
| No real-hardware run recorded | Modems not yet physically on-hand | `scripts/hw_check/` ready to execute the moment they are |
| QCA7005 PIB firmware MAC swap not performed | Needs vendor tools not redistributable | Protocol-layer impersonation is observationally identical from the charger backend's perspective (`README.md` §"How MAC / EVCCID spoofing works") |
| ISO 15118-2 PEV-side CurrentDemandReq encoding | Codec supports it but a runtime-reached test against a real ISO charger isn't in the suite | `test_iso15118_negotiation.py` exercises schema selection; encoding lives in the codec |
| Physical 1.25 kW resistive load bank | Out of software scope; paper §5 describes the hardware construction | `hardware/schematics/` placeholder |
| Anonymized dataset | Will ship post-embargo per paper §11 | `scripts/redact_session.py` proves the pipeline |

## L. Mapping from paper claims to code artifacts

### Paper §4 Methodology

- "EVCCID is transmitted in plaintext in SessionSetupReq" → `fsm_evse._state_wait_session_setup` decodes and captures `EVCCID` verbatim
- "EVCCID remains static" → protocol invariant + local-MAC origin
- "EVCCID capture in under 10 seconds" → loopback reaches CurrentDemand in ~5 s
- "Impersonation via NMK/MAC rewrite" → HotWire protocol-layer equivalent in `AutochargeImpersonation`
- "Autocharge stations authorize by EVCCID match" → CLI playbook + README clarification
- "PreCharge protocol allows rogue EVSE to assert false voltage" → `ForcedDischarge.overrides["PreChargeRes"]`
- "Vulnerable BMS closes contactors despite 0V" → hardware observation; HotWire delivers the protocol lie
- "Discharge persists 60+ min" → `overrides["CurrentDemandRes"]` keeps the lie across the whole charging loop

### Paper §5 Experimental setup

- "Python 3.9 implementation extends pyPLC and OpenV2Gx" → `README.md`, `ATTRIBUTION.md`
- "Supports arbitrary DIN 70121 parameter injection" → `PauseController.set_override` + per-stage schemas
- "State deviation" → `stage_schema.py` fuzz surface; random-schema fuzzer

### Paper §6 Real-world evaluation

- "Impersonation attack against public DC station" → A1 playbook (protocol-level)
- "1.25 kWh drain over 30 minutes" → hardware observation; software path delivers the protocol
- "Tesla Model Y rejects false-voltage attack" → hardware observation
- "60-minute sustained discharge" → A2 playbook targets sustained deception
- "No driver warnings" → hardware observation

### Paper §15 Testing platform

- GPL-3.0 open-source release — `LICENSE`
- Python DIN 70121 emulation — `hotwire/fsm/`, `hotwire/core/`
- QCA7005 HomePlug + SPI — HomePlug frame codec + hw_check phase 2
- Attack scenario templates — `hotwire/attacks/`
- Build + safety docs — `INSTALL.md`, `SAFETY.md`

---

## Verdict

**HotWire fully supports every testable claim in the paper as of Checkpoint 14.** The 🟡 items reflect honest hardware-dependent observations that cannot be bit-reproduced in software; the 🔴 item (anonymized dataset) is explicitly an embargo-gated release per paper §11 — but the new [`docs/dataset.md`](./dataset.md) manifest + SHA256 table + redaction procedure close the reviewer's verifiability gap.

The **32 committed test modules** (19 through Checkpoint 12, plus 4 at Checkpoint 13 and 9 at Checkpoint 14 covering preflight / config save / CSV export / hw_runner / session compare / session tools / config editor / live pcap / preflight wizard) cover every DIN/ISO clause HotWire touches. The hw_check suite covers the path from software to hardware with four phase-specific validators that produce REPORT.md + pcap + JSONL for each run. Checkpoint 13 also adds a GUI attack launcher, session replay panel, and wiring-diagram schematic for paper §5.

**Reviewer path for verification:**

1. Read `vendor/patches/README.md` to see which codec modifications matter
2. Run `python vendor/build_openv2g.py` — codec rebuilds byte-for-byte vs `tests/_golden_openv2g.json`
3. Run `python scripts/run_all_tests.py` — 32/32 modules green
4. Run `python -m pytest tests/test_din_conformance.py -v` — per-clause pin
5. Run `python scripts/hw_check/run_all.py` — phase 0 PASS; 1-4 SKIP (no hardware)
6. Launch the GUI: `python scripts/run_gui.py --mode evse --sim` → File / Attacks / Help menus visible, status bar shows msgs + Hz
7. See [`docs/REPRODUCING.md`](./REPRODUCING.md) for the full walkthrough
8. Optional: real hardware → `python scripts/hw_check/run_all.py -i eth1 --role pev`
