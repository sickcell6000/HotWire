# n7_a2_sustained — A2 sustained V2L evidence (real LUXGEN N7)

This bundle is the **strongest single piece of evidence in the
artifact** for the paper's §6 claim that Attack A2 (forced PreCharge
voltage / V2L bypass, HITCON ZeroDay ZD-2025-00560) can sustain
unauthorized energy extraction from a production EV's battery pack
for **tens of minutes** at a time, not just a one-shot spike.

The shorter `comprehensive_matrix/test2_a2_voltage/` capture in this
artifact shows the **mechanism** (forged 220 V → BMS closes
contactors → 5 A flows). The four files here show the **endurance**:
the same attack held for 1, 10, and 20 minutes against a real
production-VIN LUXGEN N7 in the lab.

## Files

| File | Lines | Session length | What it shows |
|---|---:|---|---|
| `n7_v2l_first_success_2025-05-19.txt` | 13,869 | first successful pull | First time HotWire's A2 path successfully induced energy export from the N7's pack on the lab bench (2025-05-19) |
| `n7_v2l_sustained_1min.txt` | 40,540 | ~1 min | Continuous V2L for 1 min, manually stopped |
| `n7_v2l_sustained_10min.txt` | 285,000+ | ~10 min | Continuous V2L for 10 min, manually stopped |
| `n7_v2l_sustained_20min.txt` | 220,000+ | ~20 min | Continuous V2L for 20 min, manually stopped |

## What you should grep for

The forged voltage and the actually-flowing current both appear as
plain JSON inside the EVSE log. To confirm A2 succeeded:

```bash
$ grep -E '"EVSEPresentVoltage.Value"|"EVSEPresentCurrent.Value"' \
        n7_v2l_sustained_10min.txt | head -10

"EVSEPresentVoltage.Value": "203",   ← forged-ramp PreCharge value
"EVSEPresentVoltage.Value": "228",
"EVSEPresentVoltage.Value": "253",
…
"EVSEPresentCurrent.Value": "200",   ← 20.0 A actually flowing
"EVSEPresentCurrent.Value": "200",   ← still 20.0 A, ten minutes in
```

`EVSEPresentVoltage` is the value HotWire's rogue EVSE *claimed* it
was sourcing onto the DC bus (the lab bench was not energised — it
was 0 V upstream of the contactors). `EVSEPresentCurrent.Value =
200` decoded against `Multiplier = -1` is **20.0 A flowing from the
N7's pack** through the rogue EVSE into the resistive load at the
attacker's side. That this same value repeats unbroken across
thousands of `CurrentDemandReq` / `Res` exchanges over 20 minutes
proves the V2L is not a transient — the pack genuinely energised the
attacker's load until the operator stopped the test.

## Capture metadata

| Field | Value |
|---|---|
| Vehicle | LUXGEN 納智捷 N7 (production VIN, owner consent) |
| Charging interface | CCS Combo 1 |
| EVSE side | HotWire on Windows + QCA7005 PLC modem + custom DC harness |
| Lab DC bus voltage | 0 V (no upstream voltage source) |
| External load | resistive load + indicator bulb across CCS DC ± |
| Sentinel forged voltage | 200–253 V (PreCharge ramp values) |
| Actual flowing current | 20 A (decoded from `EVSEPresentCurrent.Value=200, Multiplier=-1`) |
| Sustained durations | 1 min / 10 min / 20 min separate runs |
| Capture dates | 2025-05-19 onward |

## PII redaction

These files were originally captured to a verbose pyPlc/HotWire log
and contained:

- The real victim N7's EVCCID (= vehicle MAC), redacted to
  `[REDACTED-N7-EVCCID]`.
- The lab Windows machine's QCA7005 modem MAC, redacted to
  `02:00:00:00:00:01`.
- That MAC's derived IPv6 link-local, redacted to `fe80::1%lab0`.
- The lab Windows pcap NIC GUID, redacted to `NPF_{REDACTED-NIC-GUID}`.

The simulated test EVSEID (`5a5a3030303030`, ASCII "ZZ00000") is the
OpenV2G default sentinel and is left intact. Re-running the
redaction is reproducible via `scripts/redact_evse_log.py`.

## In the paper

Maps to:

- **§4** A2 attack mechanism — same code path that produces these
  logs, exercised in sim mode by `tests/test_attack_sim_mode.py
  ::test_a2_forced_discharge_sim`.
- **§6** field-test results — the "sustained V2L for tens of minutes
  on a production EV" finding underpinning HITCON ZeroDay
  ZD-2025-00560.

## Cross-reference

- Mechanism (sim mode, no hardware):
  `tests/test_attack_sim_mode.py::test_a2_forced_discharge_sim`
- Mechanism (real hardware, 60 s):
  `comprehensive_matrix/test2_a2_voltage/`
- **Endurance (real hardware, 1–20 min): this bundle.**
