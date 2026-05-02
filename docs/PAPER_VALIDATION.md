# Paper validation — complete GUI test suite

This document is the operator's run-book for reproducing every
empirical claim the WOOT '26 HotWire paper makes, using the GUI
exclusively (no CLI scripts). Each suite below maps directly to a
section of the paper, lists the exact GUI clicks, and tells you
which artifact to capture as evidence.

Three machines, three roles:

| Role | Machine | Path | Mode flag |
|---|---|---|---|
| EVSE testbed | Windows (`USER`) | `C:\Users\USER\HotWire` | `--mode evse --hw` |
| PEV testbed | Raspberry Pi 4 (`pi`) | `~/project/HotWire` | `--mode pev --hw` |
| Dev (no hw) | Windows (`sickcell`) | `C:\Users\sickcell\hotwire\HotWire` | `--mode {evse,pev} --sim` |

Pre-flight checklist for any session:

- [ ] All three repos at the same git HEAD (`git log --oneline -1`).
- [ ] `OpenV2G.exe` (Windows) / `OpenV2G` (Pi aarch64) present in
      `hotwire/exi/codec/` — re-build via `python vendor/build_openv2g.py`
      if missing.
- [ ] PLC modems wired to CCS gun, target vehicle / charging station
      ready, all safety equipment (isolation transformer, emergency
      disconnect, fire suppression) in place per `SAFETY.md`.

---

## Suite 0 — Baseline / Clean Pass (control)

**Why**: proves the HotWire stack itself is functional, so any later
attack-success result can be attributed to the protocol vulnerability
rather than a HotWire bug. Required for the paper's reviewer-rebuttal
appendix.

**Setup**
- EVSE side: open GUI, **don't apply any Override**, click **Start**.
- PEV side: open GUI, **don't apply any Override**, click **Start**.

**Expected trace progression** (in the **Combined** tab of the center
column, both machines):

```
[SLAC pev/evse] PAIRED
[CONNMGR] ConnectionLevel changed from 5 to 100
SessionSetupReq → SessionSetupRes (ResponseCode=OK_NewSessionEstablished)
ServiceDiscoveryReq → ServiceDiscoveryRes (ResponseCode=OK)
ServicePaymentSelectionReq → ServicePaymentSelectionRes
ContractAuthenticationReq → ContractAuthenticationRes (EVSEProcessing=Finished)
ChargeParameterDiscoveryReq → ChargeParameterDiscoveryRes (Finished)
CableCheckReq → CableCheckRes (EVSEProcessing=Finished)
PreChargeReq → PreChargeRes (EVSEPresentVoltage matches EVTargetVoltage)
PowerDeliveryReq → PowerDeliveryRes (EVSEStatusCode=EVSE_Ready)
CurrentDemandReq → CurrentDemandRes  ← repeats indefinitely
```

**Pass criterion**: PEV reaches `WaitForCurrentDemandRes` and the
exchange loops without `99:SequenceTimeout` for at least 30 seconds.

**Evidence to capture** *(also auto-saved by GUI)*:
- `sessions/EVSE_<timestamp>.jsonl`
- `sessions/PEV_<timestamp>.jsonl`
- Screenshot of GUI showing the Combined tab with all 14 stages
- Save trace log via `Save log…` button

---

## Suite 1 — A1 Phase 1: EVCCID Harvesting (paper §4.1.1)

**Paper claims tested**:
- EVCCID appears unencrypted in `SessionSetupReq` (claim §4.1.1)
- Capture completes in **< 10 seconds** (claim §6.1)
- EVCCID is static across sessions (claim §4.1.1)

**Roles**
- HotWire = rogue EVSE
- Target = real vehicle (one of: Tesla Model Y, Luxgen N7, CMC, Hyundai IONIQ 6)

**Steps** (per vehicle, repeat 5 ×)

1. EVSE GUI → **Clear overrides** (button bottom-right) to ensure clean state
2. Click **Start** + start a stopwatch
3. Plug HotWire's CCS gun into the victim vehicle's charge port
4. Watch the center-column **Combined** tab; first `← rx` entry
   should be `SessionSetupReq` within ~5 s of plug-in
5. Expand the entry → record the **EVCCID** field value (12 hex chars)
6. Stop the watch when `SessionSetupReq` first appears in the tree
7. Click **Stop**, unplug, log the timing

**Expected**

| Metric | Expected value | Paper §6.1 |
|---|---|---|
| Capture time per attempt | < 10 s | "completes in under 10 seconds" |
| EVCCID consistency across 5 captures | identical hex string | static identifier |
| Vehicle dashboard / app warning | none | silent capture |

**Evidence per capture**

- [ ] `sessions/EVSE_<timestamp>.jsonl` — search for `"msgName": "SessionSetupReq"` line, record EVCCID
- [ ] StatusPanel screenshot showing `EVCCID:` field populated
- [ ] Stopwatch reading
- [ ] Vehicle screen photo (no charging-related warning visible)

**Save the EVCCID** as a preset: in the GUI's Override editor for
`SessionSetupReq`, type the captured hex into the EVCCID field, click
💾, give it the label `<Vehicle> captured <date>`, add a note with
the location and any test-vehicle owner authorization detail. The
preset persists to `config/attack_presets.json` and re-appears in
both Attack Launcher and Stage Config Panel for Suite 2.

---

## Suite 2 — A1 Phase 2: Impersonation Attack (paper §6.1)

**Paper claims tested**:
- Authorization within **3.2 s** of cable connection (claim §6.1)
- Charging station's backend bills the **victim's account** (claim §6.1)
- Energy delivered = **1.25 kWh in 30 min** at 2.5 kW avg (claim §6.1)
- 7 different commercial Autocharge networks accept the spoof (Table 3)

**Roles**
- HotWire = rogue PEV
- Target = real public Autocharge-enabled charging station

**7 stations to test (paper Table 3)**

| Provider | Tested kW | Expected outcome |
|---|---:|---|
| EVALUE | 180 | session authorized |
| EVOASIS | 180 | session authorized |
| iCHARGING | 180 | session authorized |
| STAR Charger | 180 | session authorized |
| TAIL | 200 | session authorized |
| U-POWER | 360 | session authorized |
| YES! Charging | 180 | session authorized |

**Steps** (per station, repeat 3 ×)

1. PEV GUI → left column → click stage **`SessionSetupReq`**
2. In the Override editor at the bottom-left:
   - **EVCCID** field → either pick from preset dropdown (if you saved
     it in Suite 1) or type the captured 12-hex
   - Click **Apply Override**
3. Confirm `SessionSetupReq` row in the Stage Nav shows the `●` marker
4. Plug HotWire's CCS gun into the station's charging gun
5. Click **Start**, start a stopwatch
6. **Stopwatch reading at moment station LCD shows "Authorized" / victim's account name** = capture metric `t_auth`
7. Let the session run for **30 minutes**
8. Watch the **Sent (tx)** tab to verify our outbound `SessionSetupReq` carries the spoofed EVCCID
9. Watch the station's display for any anomaly / fraud-detection screen (paper claims none)
10. Click **Stop** at 30 min, unplug
11. Open the charging operator's mobile app, navigate to transaction
    history, screenshot the new session entry (it should be under
    the **victim's** account)

**Expected per station × 3 sessions = 21 records**

| Metric | Expected | Paper |
|---|---|---|
| `t_auth` | < 5 s (avg 3.2 s per paper) | §6.1 |
| Energy delivered in 30 min | ~1.25 kWh | §6.1 |
| Mobile-app billing entry | victim account | §6.1 |
| Station-side anomaly alerts | 0 | §6.1, §8 |

**Evidence per session**

- [ ] `sessions/PEV_<timestamp>.jsonl` — has the SessionSetupReq with spoofed EVCCID
- [ ] Stopwatch photo showing `t_auth`
- [ ] Station LCD photo showing "Authorized" + victim account name
- [ ] Mobile-app screenshot (transaction history) showing fraudulent session
- [ ] Power meter reading (kWh) at 30-min mark — photograph
- [ ] PCAP capture from EVSE (use `Hardware → Live pcap viewer` to
      stream `eth0`/`Ethernet 14`, save when done)

---

## Suite 3 — A2 Forced Discharge: Quick Verification (paper §4.2.2)

**Paper claims tested**:
- 3-of-4 vehicles have **trust-in-protocol BMS** (Table 4)
- Tesla Model Y refuses with **"External voltage verification failed"** (§6.2)
- Voltage offset matrix tested at **V_batt ± 0 / ± 5 / ± 10 V** (§5)

**Roles**
- HotWire = rogue EVSE
- Target = real vehicle (4 models)
- DC pins **must be wired to a passive resistive load only** — NEVER to a real DC supply

**Setup before each run**

1. EVSE GUI → for each of the three stages below, click stage in
   left nav, set fields in Override editor, click **Apply Override**.

   **`CableCheckRes`** (acks the EV's isolation check)
   - `ResponseCode` → `OK`
   - `EVSEProcessing` → `Finished`
   - `IsolationStatus` → `Valid`
   - `EVSEStatusCode` → `EVSE_Ready`

   **`ChargeParameterDiscoveryRes`** (advertises capability)
   - `EVSEMaximumVoltageLimit` → `450` (preset available)
   - `EVSEMaximumCurrentLimit` → `200` (preset available)

   **`PreChargeRes` ← the attack core**
   - `EVSEPresentVoltage` → see voltage matrix below
   - `IsolationStatus` → `Valid`
   - `EVSEStatusCode` → `EVSE_Ready`

2. Verify Stage Nav shows `●` next to all three stage names

**Voltage offset matrix (3 runs each, 4 vehicles = 36 sessions)**

| Vehicle | V_batt | Offset 0 | Offset +5 | Offset −5 |
|---|---|---|---|---|
| Tesla Model Y LR | ≈400 | 400 V | 405 V | 395 V |
| Luxgen N7 | ≈400 | 400 V | 405 V | 395 V |
| CMC | ≈400 | 400 V | 405 V | 395 V |
| Hyundai IONIQ 6 | ≈450 | 450 V | 455 V | 445 V |

Presets `400 / 405 / 395` already shipped in `config/attack_presets.json`
under scope `PreChargeRes.EVSEPresentVoltage`.

**Steps per run**

1. Set `PreChargeRes.EVSEPresentVoltage` to the matrix value, **Apply Override**
2. Plug HotWire's CCS gun into the test vehicle (vehicle owner authorization required)
3. Click **Start**, start a 180-second timer (paper §5 caps quick-verify at 180 s)
4. Watch the trace until **PreCharge phase**:
   - **Vulnerable BMS** (Luxgen / CMC / Hyundai): trace progresses past `WaitForPreChargeReq` to `WaitForPowerDeliveryReq`, contactor closes, **current flows through resistive load** ← record on multimeter
   - **Tesla**: trace shows PreChargeReq retries with `EVTargetVoltage` static, eventually session terminates with `99:SequenceTimeout` from EV side; vehicle error log shows "External voltage verification failed"
5. At 180 s OR earlier on EV-initiated abort, click **Stop**, unplug

**Expected (paper Table 4)**

| Vehicle | Vulnerable | Contactor closes? |
|---|---|---|
| Tesla Model Y LR | **NO** | refuses, error message |
| Luxgen N7 | **YES** | closes, current flows |
| CMC | **YES** | closes, current flows |
| Hyundai IONIQ 6 | **YES** | closes, current flows |

**Evidence per run**

- [ ] `sessions/EVSE_<timestamp>.jsonl` — confirms our PreChargeRes carries the lying voltage
- [ ] Multimeter photo at the resistive load (current reading)
- [ ] Tesla-only: photo of vehicle's error screen showing "External voltage verification failed"
- [ ] Combined-tab screenshot showing the protocol exchange's last 5–10 messages

---

## Suite 4 — A2 60-min Sustained Discharge (paper §6.2)

**Paper claims tested**:
- **1.25 kWh drained in 60 min** at 1.25 kW load (Table 4)
- **ΔSoC ≈ 2.1%** (§6.2)
- **Zero driver alerts** during entire 60-min discharge (Table 4)
- **BMS does not autonomously open contactors** under reverse current

**Run only on the 3 confirmed-vulnerable vehicles** (skip Tesla — it
already failed Suite 3 quick-verify, no point running 60 min).

**Setup**
- Same overrides as Suite 3 with `PreChargeRes.EVSEPresentVoltage` =
  the vehicle's V_batt (offset 0 V)
- Resistive load bank: 5 × 220 V/250 W bulbs in parallel = ~1.25 kW
  per paper §5

**Steps per vehicle (1 ×)**

1. **Before plugging**: photo of vehicle dashboard showing initial SoC
2. Apply overrides (same as Suite 3, V_batt + 0)
3. Plug, click **Start**, start 60-min timer
4. Every 5 minutes: read multimeter current, photograph
5. Watch trace + vehicle dashboard continuously for any alert (paper expects none)
6. At T = 60 min: photo of dashboard SoC, click **Stop**, unplug
7. Compute `(initial_SoC − final_SoC)` in % and `∫I·V·dt` in kWh

**Expected**

| Metric | Expected | Paper Table 4 |
|---|---|---|
| Total energy drained | 1.25 kWh ± 5% | "1.25 kWh" |
| ΔSoC | ~2.1% | "approximately 2.1%" |
| Driver alerts | 0 | "None detected" |
| Auto-opening of contactors mid-session | none | "BMS did not autonomously open" |

**Evidence per vehicle**

- [ ] Initial-SoC dashboard photo (T=0 min)
- [ ] 12 × multimeter photos at 5-min intervals
- [ ] Final-SoC dashboard photo (T=60 min)
- [ ] `sessions/EVSE_<timestamp>.jsonl` — full 60-min protocol trace
- [ ] Power-meter cumulative kWh reading at T=60 min
- [ ] Video of dashboard during random 30-second window mid-session
      (proves no warnings appeared)

---

## Suite 5 — Combined attack chain (paper §8 fleet scenario)

**Paper claim tested**:
- §8 *"Discussion"* speculates about fleet-scale attacks where one
  attacker harvests EVCCIDs and induces discharge across many
  vehicles. This suite demonstrates the chain works end-to-end on
  one vehicle.

**Steps**

1. Run **Suite 1** for one vulnerable vehicle (e.g. Luxgen N7)
   → capture `EVCCID_victim` (12 hex)
2. Disconnect, reconfigure HotWire to EVSE mode
3. Run **Suite 4 (60-min)** on that same vehicle
4. After Suite 4, reconfigure to PEV mode
5. Drive to any one Suite 2 station, run impersonation attack with
   `EVCCID_victim` for 30 min

**Pass criterion**: Same `EVCCID_victim` was used in 3 different
attack contexts (capture / discharge / replay) and all 3 succeeded.

**Evidence**:
- All artifacts from Suite 1 + Suite 4 + Suite 2 for that one vehicle
- `EVCCID_victim` appears in all three corresponding session JSONL files

---

## Suite 6 — Negative / boundary tests

**Paper claim tested**: Tesla refuses (Table 4); voltage-offset matrix
tests boundary behaviour (§5).

These cases are *expected failures* that strengthen the positive
results. They go in the paper's appendix / supplementary table.

| Sub-test | Expected | Paper |
|---|---|---|
| 6a. Suite 3 on Tesla, offset 0 V | EV refuses contactor close | Table 4 |
| 6b. Suite 3 on Luxgen, **offset +20 V** | EV refuses (delta > 5 V) | §5 boundary |
| 6c. Replay deadbeefdead (sentinel) at any station | station rejects | §6.1 control |
| 6d. EVCCID = empty at SessionSetupReq | OpenV2G fallback to deadbeefdead, station rejects | sanity |

The `deadbeefdead` preset is shipped for exactly 6c — pick it from the
PEV's `SessionSetupReq.EVCCID` dropdown, run as if it were a real attack,
expect the station's anti-fraud or *unknown EVCCID* path to fire.

---

## Evidence index — what goes into the paper

After all 6 suites run, the artifact bundle should contain:

```
datasets/paper_evidence/
├── README.md                                           ← test matrix summary
├── suite0_baseline/
│   ├── EVSE_<ts>.jsonl
│   ├── PEV_<ts>.jsonl
│   └── combined_tab_screenshot.png
├── suite1_evccid_harvest/
│   ├── tesla_model_y/{1..5}.jsonl
│   ├── luxgen_n7/{1..5}.jsonl
│   ├── cmc/{1..5}.jsonl
│   ├── ioniq6/{1..5}.jsonl
│   └── timing_summary.csv                              ← capture-time per attempt
├── suite2_impersonation/
│   ├── EVALUE/{session1,2,3}.jsonl + station_lcd.jpg + mobile_app.png
│   ├── EVOASIS/{...}
│   ├── iCHARGING/{...}
│   ├── STAR_Charger/{...}
│   ├── TAIL/{...}
│   ├── U_POWER/{...}
│   └── YES_Charging/{...}
├── suite3_discharge_quick/
│   ├── tesla_400v_run{1,2,3}.jsonl + tesla_error_screen.jpg
│   ├── luxgen_n7_400v_run{1,2,3}.jsonl
│   ├── luxgen_n7_405v_run{1,2,3}.jsonl
│   ├── luxgen_n7_395v_run{1,2,3}.jsonl
│   └── ... (12 runs per vulnerable vehicle, 36 jsonl + multimeter photos)
├── suite4_discharge_sustained/
│   ├── luxgen_n7_60min/
│   │   ├── EVSE_<ts>.jsonl
│   │   ├── soc_initial.jpg
│   │   ├── soc_final.jpg
│   │   ├── multimeter_t05.jpg ... multimeter_t60.jpg
│   │   ├── dashboard_during.mp4
│   │   └── energy_total.txt                            ← computed kWh
│   ├── cmc_60min/
│   └── ioniq6_60min/
├── suite5_combined/
│   └── luxgen_n7/                                       ← chain demo
│       ├── 1_capture/EVSE_<ts>.jsonl
│       ├── 2_discharge_60min/                            ← copy of suite4 entry
│       └── 3_impersonation_30min/                       ← copy of suite2 entry
└── suite6_negative/
    └── ... (boundary cases)
```

For the paper:
- **Suite 1 + 2** → §6.1 evidence (impersonation)
- **Suite 3 + 4** → §6.2 evidence (discharge), Table 4
- **Suite 5** → §8 fleet-scenario discussion
- **Suite 6** → appendix / supplementary table
- **Suite 0** → reviewer-rebuttal appendix

---

## GUI behaviour notes for the operator

The recent commits made the FSM forgiving so a multi-day test bench
isn't fighting the tool:

- **SLAC pairing waits indefinitely** — leave a side running with no
  cable plugged in; trace shows nothing until you plug; SLAC pairs
  the moment a peer starts on the wire.
- **V2G handshake follows DIN 70121 §9.6 spec timeouts** — 2 s msg /
  5 s CurrentDemand / 60 s sequence, matching real EVs. If you let
  a session stall (e.g. forgot to set an Override), it'll go through
  Safe-Shutdown and land in `STATE_END` / `STATE_STOPPED`.
- **End / Stopped states auto-reInit after ~5 s** — no need to click
  Reset FSM between attempts. Just wait for the trace to show
  `auto-reinitializing for next session`, then re-plug or re-run.
- **Stop → Start cycle works** — earlier `connMgr.sys.exit()` issue
  is fixed (`run_gui.py` overrides `exit_on_session_end = False` and
  `worker_thread.run` catches `SystemExit` defensively).

If you ever do need to force a clean restart:
- **Reset FSM** button — re-arms the FSM in place (no socket teardown)
- **Stop** + **Start** — full worker teardown + rebuild

---

## Where things live (quick reference)

| Need | Location |
|---|---|
| Per-stage Override editor | Bottom-left column of GUI (was middle, swapped in `aa05bb9`) |
| Save value as preset | 💾 button next to each editable field |
| Manage / delete presets | ⚙ button next to each editable field |
| Preset JSON file | `config/attack_presets.json` (hand-editable) |
| Combined / Received / Sent message tabs | Center column, three tabs |
| Live trace log | Right column |
| Session JSONL output | `sessions/EVSE_<ts>.jsonl` + `PEV_<ts>.jsonl` |
| Attack-launcher dialog | `Attacks → Launch attack…` (menu bar) |
| Reset FSM mid-session | `Reset FSM` button (right column, second row) |

Sleep well — this should let you run end-to-end tomorrow without me.
Anything that fails in a way the doc doesn't cover, paste the trace
back to me and we'll patch it.
