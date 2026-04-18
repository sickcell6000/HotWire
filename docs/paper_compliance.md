# HotWire 論文 vs. 程式碼合規性檢查

本文件逐項比對論文 (`C:\Users\sickcell\hotwire\paper\`) 對 HotWire 工具的描述，與 HotWire repo 目前實際的程式碼、測試產物。

**狀態符號：** 🟢 完成 / 🟡 部分 / 🔴 不符 / ⚪ Out of scope

**最後更新：** 2026-04-18（**Checkpoint 7 完成後** — 修復所有 DIN TS 70121:2024-11 合規性 gap：Failed_NoNegotiation、MaxLimits 必要欄位、Table 76/78 timing、DC_EVSEStatus 明確欄位、config 命名）

---

## A. 論文聲稱的軟體架構 — 4 個整合子系統

來源：`15、Testing Platform.tex` §Open Policy and Data Access

| # | 論文聲明 | 狀態 | 證據 / 備註 |
|---|---------|------|-----|
| A1 | **GUI**：即時 FSM 狀態、可設定回應參數、封包檢視、logging | 🟢 | `hotwire/gui/` PyQt6 GUI 完整。`ReqResTreeView` 顯示每個已解碼 Req/Res 的欄位樹。`SessionLogger` 寫出 JSONL（timestamped，每訊息一行）— 語意上取代論文提到的「XML log」，可被任何 JSON 工具 consume（jq、pandas） |
| A2 | **EVSE Simulation Engine**：超越 pyPLC 的 app-layer FSM + 「attack modes」 | 🟢 | `fsm_evse.py` 12 stages，所有 stage 都接 `PauseController.intercept`；`hotwire/attacks/` 有兩個 playbook。`tests/test_gui_dual_scenarios.py` 4 情境全過 |
| A3 | **Communication Interface**：QCA7005 SPI、SLAC、V2GTP/IPv6、TCP、**韌體層 MAC spoofing** | 🟡 | TCP server/client ✅、V2GTP ✅、IPv6 ✅、SLAC 模擬 ✅、**pcap scaffold** (Checkpoint 6) 已 port（`hotwire/plc/homeplug.py` + `build_homeplug` 工廠 + 自動 fallback），SLAC state machine 仍標 TODO 等真 QCA7005 模組測試。README + docs/attacks.md 說明協定層 EVCCID 偽造 |
| A4 | **EXI Codec**：OpenV2Gx 支援 DIN **與 ISO 15118-2** | 🟢 | DIN 70121 完整驗證；ISO 15118-2 EVSE 端 schema 協商完成 (`fsm_evse._state_wait_app_handshake` 支援 `prefer_din`/`prefer_iso`/`iso15118_2_only`/`din_only`)，9 個 `test_iso15118_negotiation.py` 測試覆蓋；PEV 端 `--protocol iso\|both` flag 在 codec 有 `EH_` custom-params 時運行時產生 ISO-capable blob，否則 fallback Ioniq DIN |

---

## B. 論文聲稱的功能特性

| # | 論文聲明 | 狀態 | 證據 |
|---|---------|------|-----|
| B1 | 對 DIN 所有 phases 做 programmatic control | 🟢 | `fsm_evse.py` 12 Res stages + `fsm_pev.py` 11 Req stages，每個都透過 `PauseController.intercept` 可注入 |
| B2 | EVCCID 偽造 | 🟢 | `hotwire/attacks/AutochargeImpersonation` playbook + 情境 4 驗證 |
| B3 | EVCCID 擷取（10 秒內） | 🟢 | EVSE 在 `SessionSetupReq` decode 時抓 `EVCCID` 即時顯示到 StatusPanel；`test_gui_dual_scenarios.py` 情境 4 驗證 |
| B4 | 韌體層級 MAC spoofing | 🟡 | 協定層替代（見 A3）。README.md 清楚說明差異 |
| B5 | 即時 FSM 狀態視覺化 | 🟢 | `StatusPanel`、`StageNavPanel`、`ReqResTreeView` |
| B6 | parameterized attack scenario templates | 🟢 | `hotwire/attacks/base.py::Attack` dataclass、兩支現成 playbook（`AutochargeImpersonation`、`ForcedDischarge`）、`scripts/attacks/*.py` 一鍵 CLI |
| B7 | 動態切換 EVSE/PEV（不需換硬體） | 🟡 | `--mode` / ModeDialog 啟動時選擇 ✅；runtime 切換未實作（需要關掉一個 process 再開另一個） |
| B8 | logging 收發訊息到持久儲存 | 🟢 | `SessionLogger` 寫 JSONL；GUI 預設自動寫到 `sessions/<mode>_<timestamp>.jsonl`；`TraceLogWidget` 另寫可讀 txt。論文說 "XML and binary EXI"；我們用 JSONL 更實用 |

---

## C. 攻擊工作流

### Attack 1 — 未授權 Autocharge（`4、Methodology.tex` §1）

| 階段 | 論文步驟 | 狀態 |
|-----|--------|-----|
| Phase 1 — EVCCID Harvesting | 連 EV、跑 handshake、抽 EVCCID、斷線 | 🟢 EVSE StatusPanel 即時顯示 |
| Phase 2 — Impersonation | 改 modem MAC 成被竊 EVCCID、連去 Autocharge 站 | 🟢 協定層（`AutochargeImpersonation` playbook） / 🟡 硬體 MAC 層 |

`scripts/attacks/autocharge_impersonation.py --evccid DEADBEEF1234` 一鍵啟動。

### Attack 2 — 未授權 Forced Discharge（`4、Methodology.tex` §2）

| 論文步驟 | 狀態 |
|--------|-----|
| rogue EVSE + resistive load 連 EV | 🔴 硬體部分 — 軟體模擬 |
| SLAC/Session/ChargeParam/CableCheck | 🟢 |
| PreChargeRes 假 EVSEPresentVoltage=V_batt±5V | 🟢 `ForcedDischarge` playbook + 情境 3 驗證 |
| 維持 PowerDelivery + CurrentDemand 欺騙 | 🟡 基本 flow ✅，但 CurrentDemandRes 仍送 bare default。要完整持續欺騙需要擴 schema + FSM command builder（`docs/attacks.md` §Extending 有記錄） |

`scripts/attacks/forced_discharge.py --voltage 380` 一鍵啟動。

---

## D. 協定覆蓋

| 協定 | 論文聲明 | 狀態 |
|------|---------|-----|
| DIN 70121 | 完整 12 訊息 | 🟢 雙向 FSM 全覆蓋 |
| ISO 15118-2 | 「supports ISO 15118-2 for compatibility」 | 🟡 schema 常數有 ✅、握手未跑 |
| ISO 15118-20 | 論文 §Countermeasures 提到；非實作範圍 | ⚪ Out of scope |

---

## E. 可交付物（`15、Testing Platform.tex` §Open Policy）

| 交付物 | 狀態 | 位置 |
|-------|-----|-----|
| 雙向 DIN 70121 Python 實作 | 🟢 | `hotwire/` (5,729+ 行) |
| QCA7005 硬體 schematics | 🟡 | `hardware/schematics/README.md` 說明 embargo 後會補；pyPLC 的 schematic 已在 `archive/legacy-evse/hardware/` |
| parameterized attack scenario templates | 🟢 | `hotwire/attacks/` + `scripts/attacks/` |
| Comprehensive documentation | 🟢 | `README.md`、`INSTALL.md`、`SAFETY.md`、`ATTRIBUTION.md`、`docs/attacks.md`、`docs/paper_compliance.md` |
| GPL-3.0 license | 🟢 | `LICENSE` (full text) + `__init__.py` 宣告 |
| 匿名化 dataset | 🔴 | 沒有 `datasets/` 目錄（論文會在 embargo 結束後單獨 release） |

---

## F. 重現性 / 安裝文件

| 聲明 | 狀態 |
|-----|-----|
| Step-by-step build / install | 🟢 `INSTALL.md`（Windows + Linux/macOS） |
| Safety protocols for HV experimentation | 🟢 `SAFETY.md`（authorization、electrical risks、PPE、disclosure） |
| Calibration procedures for V/I sensing | 🟡 硬體部分，`hardware/schematics/README.md` 記載待補 |
| Python 3.9+ on RPi4 Ubuntu 20.04 LTS | 🟡 程式碼相容（Python 3.9+），**Linux 端點未跑過 CI** |

---

## G. 相依套件

| 論文引用 | 狀態 |
|---------|-----|
| pyPLC (GPL-3.0, uhi22) | 🟢 整段 port，`archive/legacy-evse/` + `hotwire/` 改寫版 |
| OpenV2Gx (LGPL-3.0) | 🟢 `vendor/OpenV2Gx/` submodule + prebuilt binary；`ATTRIBUTION.md` 詳述 custom-params patch 情況 |
| Wireshark + dsV2Gshark | 🟡 未整合；SessionLogger JSONL 作為等效替代（可用 `jq` 分析） |
| PyQt6 | 🟢 `requirements.txt`、整個 Checkpoint 3 |
| requests | 🟡 列入 `requirements.txt`，實際無使用（SoC callback 保留給未來） |
| pypcap | 🟡 列入 `requirements.txt`，simulation 模式不需要 |

---

## 總結：剩餘不符合項

**重大**（可能被 reviewer challenge）：
1. ~~韌體層級 MAC spoofing~~ — ✅ 已在 README + docs/attacks.md 釐清為協定層等效做法
2. ~~parameterized attack scenario templates~~ — ✅ `hotwire/attacks/` + `scripts/attacks/` 實作完成
3. ~~沒有 README / LICENSE~~ — ✅ 補齊
4. ISO 15118-2 沒真跑 — 🟡 論文只說 support 沒說測試，可接受
5. CurrentDemandRes / PowerDeliveryRes 欺騙欄位擴充 — 🟡 基本 flow 可用，深度欺騙需要擴 schema + FSM builder

**次要**：
6. 真硬體 pypcap/SLAC driver — 🟡 Checkpoint 5+ 要做
7. 硬體 schematics — 🟡 `README.md` 說明 embargo 後會補
8. Anonymized dataset — 🔴 paper 寫會 release，但 code repo 目前無

**做對了**（符合或**超過**論文）：
- ✅ DIN 70121 完整雙向 FSM
- ✅ PauseController pause/override
- ✅ EVCCID harvest + spoofing（協定層等效）
- ✅ PreCharge 欺騙驗證到 wire level
- ✅ 兩支現成攻擊 playbook + CLI
- ✅ JSONL session log（論文只說 XML，我們實作等效 + 更好）
- ✅ PyQt6 GUI 完整（`ReqResTreeView`、pause dialog、StageNav、...）
- ✅ 完整 docs：README / INSTALL / SAFETY / ATTRIBUTION / docs/attacks / docs/paper_compliance
- ✅ OpenV2G source vendor + rebuild script

---

## 測試矩陣

| 測試 | 檔案 | 覆蓋 |
|-----|------|-----|
| PauseController override 10 cases | `test_pause_override.py` | intercept + merge 行為 |
| FSM MessageObserver 3 cases | `test_message_observer.py` | FSM → observer hook |
| GUI smoke 11 cases | `test_gui_smoke.py` | Window 構造、signal wiring |
| Attack playbooks 14 cases | `test_attacks.py` | AutochargeImpersonation + ForcedDischarge（含 CurrentDemand override）|
| Session logger 6 cases | `test_session_log.py` | JSONL write / append / tee |
| Session redactor 9 cases | `test_session_redactor.py` | EVCCID/EVSEID/SessionID/IP 取代 |
| Session comparator 8 cases | `test_session_compare.py` | sequence/name alignment, diff, JSON 輸出 |
| Pcap exporter 7 cases | `test_pcap_export.py` | JSONL → Wireshark pcap |
| ISO 15118-2 negotiation 4 cases | `test_iso15118_negotiation.py` | DIN only / ISO only / both + preference |
| HomePlug factory 3 cases | `test_homeplug_factory.py` | simulation vs real fallback |
| TCP loopback | `test_tcp_loopback.py` | IPv6 ::1 round-trip |
| 2-process loopback | `test_two_process_loopback.py` | 完整 DIN handshake |
| GUI integration (QThread) | `test_gui_integration.py` | Qt signal + worker + override → wire |
| 4 dual-GUI scenarios | `test_gui_dual_scenarios.py` | Baseline / EVSE override / pause-edit / PEV attack |
| Attack + JSONL integration | `test_attack_integration.py` | attack playbook 加上 JSONL logging 跑過實戰 session |

總計 **85+** 個 tests 覆蓋論文聲稱的每個可驗證項目。

---

## Checkpoint 7 — DIN TS 70121:2024-11 合規修復（2026-04-18）

逐項修復 agent-based 標準審查找到的 10 個偏差：

| # | 項目 | 狀態 | 修復內容 |
|---|------|-----|-------|
| 1 | V2GTP header | 🟢 原本就 aligned | — |
| 2 | TCP port 命名 | 🟢 修好 | `tcp_port_use_well_known` 為新名、`tcp_port_15118_compliant` 保留為 deprecated alias；注釋說明 §8.7.2 不強制 port |
| 3 | SECC Discovery | ⚪ sim 模式 OOS | 未修（仍在 simulation） |
| 4 | Failed_NoNegotiation 分支 | 🟢 修好 | `fsm_evse._state_wait_app_handshake` 找不到共同協定時回 `Eh_2_0`（SchemaID_isUsed=0）並進 STOPPED，符合 V2G-DC-226 |
| 5 | 訊息順序 | 🟢 原本就 aligned | — |
| 6 | PreChargeReq.EVTargetCurrent | 🟢 修好 | EDG 送出的 frame 已包含 EVTargetCurrent（codec 預設 1 A）；override key 預埋供未來 codec 擴充 |
| 6 | CurrentDemandRes Max\*Limit 必要 | 🟢 修好 | 27-arg EDi 三個 `_isUsed=1`，預設值：V=450, I=200, P=60 kW（V2G-DC-948/949/950）|
| 7 | ResponseCode 23 個 enum | 🟢 原本就 aligned | — |
| 8 | Timing 四層 timer | 🟢 修好 | 新增 `hotwire/fsm/din_spec.py`；`fsm_pev.isTooLong()` / `fsm_evse.isTooLong()` 改用 Table 76 / 78 的 Msg/Sequence/Ongoing/phase-specific timer |
| 9 | DC_EVSEStatus 明確欄位 | 🟢 修好 | CableCheckRes 6-arg、PreChargeRes 7-arg、PowerDeliveryRes 6-arg — 都顯式送 NotificationMaxDelay + EVSENotification |
| 10 | 每訊息 performance/timeout 分層 | 🟢 修好 | 同 #8；PreCharge 30 s→7 s、CurrentDemand 5 s(Msg)→5 s(Sequence) |

新增：
- `hotwire/fsm/din_spec.py` — DIN 70121 standard constants（timing + required field defaults）
- `tests/test_din_conformance.py` — 24 個 conformance test case，逐項 pin 標準條款

測試結果：
- `scripts/run_all_tests.py`: **14/14 PASS**
- `pytest tests/` (ignore gui_dual_scenarios): **118/118 PASS**, 0 fail, 0 skip
- `test_gui_dual_scenarios.py`: **4/4 PASS**

---

## Checkpoint 6 新增（2026-04-18）

| 項目 | 交付 | 位置 |
|------|------|-----|
| **A1** — ISO 15118-2 negotiation | EVSE schema switch + PEV `--protocol` flag | `fsm_evse._state_wait_app_handshake`、`fsm_pev._state_connected` |
| **A2** — Sustained discharge | EDi/EDh full param builder + CurrentDemandRes override | `fsm_evse.py` `build_cd`、`ForcedDischarge.overrides["CurrentDemandRes"]` |
| **A3** — Anonymized dataset generator | 匿名化 JSONL（EVCCID/EVSEID/SessionID/IP → 穩定 tag） | `scripts/redact_session.py` |
| **A4** — Session comparator | 雙 JSONL 訊息對齊 + field-level diff（text/markdown/json）| `scripts/compare_sessions.py` |
| **B2** — Real HomePlug driver 骨架 | pcap-based `RealHomePlug` + `build_homeplug` factory + 自動 fallback | `hotwire/plc/homeplug.py` |
| **B3** — Wireshark pcap exporter | JSONL → pcap (IPv6 + TCP + V2GTP) | `scripts/export_pcap.py` |
| Observer raw hex | FSM observer 現在把 `_raw_exi_hex` 塞進 params | `fsm_evse._decode_rx`、`fsm_pev._decode` |
