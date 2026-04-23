# Reviewer Verifiability Audit — 2026-04-23

**Question**: For every factual/quantitative claim in the HotWire paper, can a
third-party reviewer verify it with the released artifacts?

**Method**: For each extracted claim, classify the verifiability into one of
four tiers. Tier A claims are bulletproof; Tier D claims are the ones that
will get the paper sent back for revision.

## Verifiability tiers

| Tier | Meaning | Action needed |
|---|---|---|
| **A** | Reviewer can verify fully from released artifacts (code + docs + pcaps) on their own machine | Keep as-is |
| **B** | Reviewer can verify **conceptually** but not reproduce the numeric result (e.g. "we tested 4 cars" — reviewer can't test 4 cars but trusts the methodology) | Soften language; describe setup in more detail |
| **C** | Claim depends on **unreleased data / private infrastructure / specific hardware** that the reviewer cannot access | Either release the data, or explicitly mark as "demonstration not reproducible without specific hardware" |
| **D** | Claim is **not currently backed** by anything in the repo — pure assertion | **MUST fix before submission** — add evidence, remove claim, or soften to hypothesis |

---

## Section 1: Introduction

| Claim | Tier | Evidence / Gap |
|---|---|---|
| 75-95% of new vehicle sales by 2030 are EVs | A | Citation [wri2023]; reviewer checks source |
| 88% of public stations use DIN 70121 / no TLS | B | Citation [CurrentAffairs]; reviewer trusts source but can't independently measure |
| EVExchange validated only in MiniV2G simulation | A | Citation [conti2022evexchange]; verifiable from original paper |
| **First EVCCID impersonation on production infrastructure** | **D** | No video/pcap of real attack on public charger. Claim rests entirely on author testimony |
| **Stole 1.25 kWh from commercial charging network** | **D** | Not reproducible by reviewer — attack on real station in "East Asian country". Need pcap + billing screenshot supplement |
| **60-75 kWh session = $35-45 US / $62-81 EU** | B | Citations exist; reviewer arithmetic-verifies |
| **Release low-cost emulator at $180 each** | A | `docs/hardware_design_guide.md` has full BOM; reviewer can price-check |
| **Validated against 7 charging networks** | D | No public log of which 7, which runs, which dates. Only Table 2 lists names |
| **4 vehicle models (Tesla, Luxgen, CMC, Hyundai)** | C | Specific vehicles not releasable (owner NDA); pcaps are redacted |
| **75% prioritize protocol over physical safety** | C→D | 3/4 split is testable in principle but requires same 4 cars |
| **Two manufacturers committed firmware updates** | C | Private disclosure; reviewer trusts author |

---

## Section 2: Background

| Claim | Tier | Evidence / Gap |
|---|---|---|
| HomePlug GPHY is the PHY | A | Standard citation |
| SLAC in ISO 15118-3 | A | Standard citation |
| DIN uses ephemeral ports (49152-65535), ISO uses 15118 | A | Reviewer verifies in pcap captures |
| EVCCID is 6-byte MAC | A | Verifiable in DIN 70121 spec §8.4 |
| All messages plaintext over TCP | A | Reviewer opens `datasets/preview/IONIQ6_good.pcapng` in Wireshark |
| TLS rarely deployed in ISO 15118-2 | B | Citation [CurrentAffairs] |
| ISO 15118-20 enforces TLS 1.3 | A | Standard citation |
| DIN 70121 doesn't mandate BMS voltage verification | A | Reviewer reads spec |

**Section 2 score: all A/B. Clean.**

---

## Section 3: Threat Model

| Claim | Tier | Evidence / Gap |
|---|---|---|
| Authentication rarely deployed | B | Citation |
| Tesla charge port via wireless replay | A | Citation [CVE-2022-27948] |
| **EVCCID retrievable within seconds** | A | `sim_loopback.sh` shows SessionSetup in ~2.7s; `scripts/hw_check/phase4_v2g.py` + `sustained_attack_runbook.md` document real-bench timing |
| **Device under $180 using off-the-shelf parts** | A | `docs/hardware_design_guide.md` has BOM |
| Attack goals A1, A2 described | A | `hotwire/attacks/autocharge_impersonation.py` and `hotwire/attacks/forced_discharge.py` exist — reviewer reads code |
| Scope excludes ISO 15118-20 | A | Authorial scoping |

**Section 3 score: clean.**

---

## Section 4: Methodology

| Claim | Tier | Evidence / Gap |
|---|---|---|
| HotWire = 2 attacks | A | 2 files in `hotwire/attacks/` |
| Both use physical CCS access | A | Described in methodology |
| **"Affects millions of EVs"** | **D** | No citation, handwave. **Remove or cite**. |
| EVCCID 12-hex example `d83add22f182` | A | Valid format |
| **SLAC-through-SessionSetup in ~5s on bench** | A | `sim_loopback.sh` measurement confirms ~2.7s startup to Connected state, full handshake ~5s to CurrentDemand |
| **End-to-end EVCCID capture <10s on real station** | **D** | No pcap in repo demonstrates this. Need `datasets/preview/realcharger_evccid_capture.pcapng` with timestamped frames |
| EVCCID derived from controller MAC | A | Reviewer verifies in pcap |
| **MAC reconfig via PIB using open-plc-utils** | A | `docs/hardware_design_guide.md` documents PIB programming |
| **Attack sends PreChargeRes with fake voltage V_batt±5V** | A | `hotwire/attacks/forced_discharge.py` — reviewer reads the ~30 lines that craft the fabricated response |
| Override at both PreChargeRes + CurrentDemandRes | A | Same file |
| **1.25 kW × 60 min = 1.25 kWh** | A | Arithmetic; also recorded in `sustained_attack_runbook.md` |

---

## Section 5: Experimental Setup

| Claim | Tier | Evidence / Gap |
|---|---|---|
| **Total platform $360 ($180 each)** | A | Itemized BOM in `docs/hardware_design_guide.md` |
| Python 3.9; built on pyPLC + OpenV2Gx | A | `requirements.txt` + `README.md` |
| **TP-Link PA4010 ($10), Pi4 ($70), coupling ($50)** | A | Hardware guide lists these |
| **5× 220V/250W bulbs in parallel** | A | Specified in §5 |
| **Current monitor + safety interlocks ($40)** | B | Not schematicized in repo — photo exists in paper; reviewer relies on photo |
| **Table 1: Tesla MY, Luxgen n7, CMC, IONIQ6** | C | Can't verify without the same cars |
| **7 stations: EVALUE, EVOASIS, iCHARGING, STAR, TAIL, U-POWER, YES!** | C | Reviewer can visit these in East Asia but cannot reproduce billing-under-victim-account |
| **Tested unit 180-360 kW vs operator max 640 kW** | C | Station-specific info; trust author |
| **IRB approved + written consent** | C | Must rely on author statement |
| **5 EVCCID attempts / vehicle** | C | Records not released (privacy) |
| **Wireshark + dsV2Gshark** | A | Tool reproducible |
| **3 sessions / station for impersonation** | C | Same as above |
| **Mobile-app billing monitored** | C | Private billing data |
| **3 runs × voltage offset ±0/5/10V** | C | Requires same vehicles |
| **180s max per run** | A | `sustained_attack_runbook.md` parameter |

---

## Section 6: Real-World Evaluation ⚠️ **most contested**

| Claim | Tier | Evidence / Gap |
|---|---|---|
| **A1 against major East Asian network** | **D** | No pcap, no screen capture, no billing proof released. This is the **single most important claim in the paper** and it currently rests on author testimony |
| **Auth within 3.2s of cable connect** | **D** | Same — no log released |
| **1.25 kWh over 30 min** | **D** | Same |
| **Billing confirmed to registered owner, no fraud detection** | **D** | Same. A responsible reviewer will demand redacted screenshots of billing history as supplementary material |
| Figure: impersonation.png, discharge.png | A | `paper/` has the figures; reviewer inspects |
| **Table 3 per-vehicle results** | C→D | Aggregated data, not per-run traces |
| **Tesla terminates (HW voltage sensor)** | **C** | `teslaEndWithPrecharge.pcapng` is cited — **is this in `datasets/preview/`?** Let me check below |
| **Other 3 vehicles vulnerable** | C | Their pcaps? Not in repo preview dir |
| **60min × 1.25kW → 2.1% SoC reduction** | A | Arithmetic given SoC capacity |
| **8h overnight → 25% drain extrapolation** | A | Arithmetic |
| **No driver alerts during 60min** | C | Only in-person testimony |

---

## Section 7: Countermeasures

| Claim | Tier | Evidence / Gap |
|---|---|---|
| OCPP 2.0.1 has stronger auth | A | Standard |
| HMM / ML anomaly detection works | B | Citations |
| Vulnerable BMS has voltage-sense HW unused | C | Author statement from disclosure |
| **2/4 manufacturers committed OTA updates** | C | Private |
| **Tesla refuses false voltage** | C | `teslaEndWithPrecharge.pcapng` — if released, becomes **A** |
| **Luxgen, CMC, Hyundai trust protocol state** | C | Needs their pcaps released (redacted) |
| ISO 15118-20 eliminates both attacks | A | Protocol-level argument |

---

## Section 8: Discussion

Section 8 mostly restates §6 & §7 claims. Same tier ratings apply.

**Commented-out 75% vulnerability rate (3/4)**: This is in the source but not active. If you uncomment, it enters D tier (aggregate with no raw data released).

---

## Section 9: Related Work

| Claim | Tier | Evidence / Gap |
|---|---|---|
| Baker 2019 SDR sniff | A | Cited paper |
| Brokenwire EM disruption | A | Cited paper |
| Portulator signal injection | A | Cited paper |
| PIBuster HomePlug firmware DoS | A | Cited paper |
| EVExchange MiniV2G-only validated | A | Cited paper |
| **DrainDead 11 vehicles** | A | Cited paper [draindead2025] |
| **Concurrent independent discovery (EU vs East Asia)** | B | Reader takes on faith but timeline from [draindead2025] corroborates |
| Plaka2023 ecosystem cascade | A | Cited paper |

**Section 9 is clean.**

---

## Section 10: Ethics & Disclosure ⚠️ **second most critical**

| Claim | Tier | Evidence / Gap |
|---|---|---|
| **Pre-registered accounts only** | C | Author testimony |
| **1.25 kWh paid via our account** | C | Same |
| EVCCID from own / NDA-consented vehicles | C | Same |
| **Continuous safety monitoring** | C | Same |
| **Emergency contactor, current limit, 10% SoC trip** | B | Described in `docs/ethics_evidence.md`? **Should match** |
| **No battery damage** | C | Author statement |
| **150 days pre-submission disclosure** | C | Author statement — **should include dates in paper** |
| **2/4 manufacturer reports, 7/7 station reports** | C | Private |
| **National vulnerability platform submission** | C | Evidence could be an embargoed case number |
| **2 manufacturers committed OTA, 6-12 month timeline** | C | Private |
| **Stations haven't deployed MFA for Autocharge** | C | Author observation |
| **180-day embargo OR 75% patches before release** | A | Policy statement; reviewer verifies repo state at publication |
| **Won't release EVCCID/station IDs** | A | Verifiable in repo — `datasets/` has **only** `IONIQ6_good.pcapng` preview |

**Section 10 is 80% author-testimony**. Reviewer needs one piece of external corroboration: either **publicly disclosed CVE number** from the national vulnerability platform, or **manufacturer acknowledgement letter excerpt**.

---

## Section 11: Open Policy & Data Access

| Claim | Tier | Evidence / Gap |
|---|---|---|
| GPLv3 license | A | Verifiable in `LICENSE` file |
| Complete Python DIN 70121 emulator | A | `hotwire/fsm/fsm_pev.py`, `fsm_evse.py` exist |
| Hardware schematics for QCA7005 PLC | B | `docs/hardware_design_guide.md` has schematics/guides; no KiCad files for QCA7005 specifically (pyPLC has them, not HotWire) |
| Attack scenario templates | A | `hotwire/attacks/autocharge_impersonation.py`, `forced_discharge.py` |
| Anonymous.4open.science URL | A | Reviewer visits URL — **check it works** |
| Anonymized BMS behavioral profiles | **D** | **`datasets/preview/` has only 1 pcap — where are the "behavioral profiles"?** |
| **Anonymization via `scripts/redact_session.py`** | A | Script exists in repo |
| SHA256 manifest in `docs/dataset.md` | A | File exists; reviewer verifies hashes |

---

## Section 12: Conclusion

Restates top claims. Same tier as Section 1.

---

## Section 15: Testing Platform

Active claims overlap with Section 11. **Commented-out section has MIT license claim which conflicts with active GPLv3** — must resolve before camera-ready.

---

## Aggregate verdict

| Tier | Count (approx) | Examples |
|---|---|---|
| **A** (reviewer can verify) | ~30 | OpenV2G codec, arithmetic, standards citations, GPLv3 license, `hotwire/attacks/*.py` inspection |
| **B** (conceptual verification) | ~15 | Citations to external papers; BOM pricing |
| **C** (hardware/private-data dependent) | ~35 | All 4-car test results, all 7-station test results, disclosure specifics |
| **D** (currently unbacked) | ~10 | **The most important claims in the paper** — "first A1 on production", "1.25 kWh stolen", "auth in 3.2s", "no fraud detection triggered", "affects millions of EVs" |

---

## Critical D-tier claims that will get paper rejected if unfixed

### Must fix before submission

1. **"Successfully stole 1.25 kWh from production Autocharge network"** (§1, §6)
   - Action: Release **redacted** pcap showing authentication ACK from production station, billing screenshot with PII masked, station identifier salted/hashed

2. **"Authorization within 3.2s of cable connection"** (§6)
   - Action: Release timing log with timestamps from that specific session

3. **"No fraud detection triggered during 30-min session"** (§6)
   - Action: Reference the station's public fraud-detection page OR note "anecdotal; full logs are restricted"

4. **"Affects millions of EVs and charging stations"** (§4)
   - Action: Add citation for DIN 70121 deployment numbers OR remove

5. **"Two manufacturers committed firmware updates"** (§1, §6, §7, §10)
   - Action: Include a CVE number, disclosure case ID, or redacted excerpt from response letter

6. **"Anonymized BMS behavioral profiles"** (§11)
   - Action: Either **release them** (anonymized CSVs / JSON), or **retract this promise**

### Highly recommended

7. **Tesla termination pcap** (§6, §7)
   - `teslaEndWithPrecharge.pcapng` is referenced — **make sure it's in `datasets/preview/`** (currently only `IONIQ6_good.pcapng` is there)

8. **Forced-discharge pcaps for 3 vulnerable vehicles** (§6)
   - Release redacted pcaps (EVCCID SHA256'd)

9. **Per-vehicle Table 3 raw data** (§6)
   - At minimum: session-start timestamp, session-end timestamp, voltage offset used, outcome

10. **Fix MIT vs GPLv3 conflict in §15 commented block** (§15)
    - Remove or update to GPLv3

---

## What reviewers CAN verify right now (Tier A evidence)

On their own machine, with no special hardware, within 30 minutes:

```bash
# 1. Clone repo
git clone https://anonymous.4open.science/r/HotWire-0A1F/

# 2. Read attack code
cat hotwire/attacks/forced_discharge.py
cat hotwire/attacks/autocharge_impersonation.py

# 3. Run simulation to verify FSM completeness
./scripts/sim_loopback.sh 25
# -> confirms all 13 DIN 70121 states

# 4. Run Docker CI to verify 240-test regression
docker compose run --rm hotwire-ci
# -> 240 passed, 0 failed

# 5. Inspect preview pcap
wireshark datasets/preview/IONIQ6_good.pcapng

# 6. Verify BOM in hardware design guide
# -> matches claimed $180

# 7. Re-run parametric matrix
./scripts/sim_matrix.sh         # 9 voltage×duration PASS
./scripts/sim_protocol_matrix.sh # 4 protocols PASS
```

**This covers ~30 claims (Tier A)**. Paper can defend these unreservedly.

---

## Strategy recommendation

1. **For the 10 D-tier claims**: either upgrade to A/B with released evidence, or **explicitly mark as "author testimony"** in the paper with responsible-disclosure rationale
2. **For C-tier hardware/private claims**: add a **supplementary materials statement** explaining which data is restricted and why (IRB, NDA, manufacturer confidentiality)
3. **Add a "Verifiability Table"** to the paper appendix — literally this audit's format: one row per load-bearing claim with "reviewer can verify via X" or "restricted due to Y"

The paper is **not currently in rejection territory**, but ~10 claims need shoring up. The simulation/framework half (§4, §5, §11) is solid. The real-world attack half (§6, §10) is the exposure.
