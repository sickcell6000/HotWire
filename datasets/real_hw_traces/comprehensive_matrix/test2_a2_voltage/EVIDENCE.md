# test2_a2_voltage — Attack A2 evidence summary

This bundle is a real-hardware capture of **Attack A2 (Forced
PreCharge / unauthorized energy extraction / V2L bypass)** running
between a HotWire-controlled rogue EVSE and a real LUXGEN N7 vehicle.
It is the strongest single piece of evidence in this artifact, and it
underpins HITCON ZeroDay ZD-2025-00560.

## What you should see

The EVSE was configured to **always** report
`EVSEPresentVoltage = 220 V` regardless of the actual DC bus state.
The bus during this capture was **not** carrying 220 V — the EVSE
side had no voltage source connected, only an external resistive load
representing a parasitic V2L draw. The vehicle's BMS, trusting the
protocol-level voltage reading over its own physical inlet sensor,
closed its high-voltage contactors and sourced current into the
attacker-controlled load.

Grep the session for the forged voltage:

```bash
$ grep '"EVSEPresentVoltage.Value": "220"' pev/session.jsonl | head -3
… "msgName": "PreChargeRes",
   "EVSEPresentVoltage.Value": "220",
   "EVSEStatusCode_text": "EVSE_Ready" …
… "msgName": "CurrentDemandRes",
   "EVSEPresentVoltage.Value": "220",
   "EVSEPresentCurrent.Value": "5",   ← 5 A *actually flowing* into load
   "EVSEStatusCode_text": "EVSE_Ready" …
```

`EVSEPresentVoltage = 220 V` at every PreChargeRes / CurrentDemandRes
in the session, and `EVSEPresentCurrent = 5 A` shows the BMS
genuinely sourced 5 A into the rogue EVSE's load — the high-voltage
contactors closed and energy left the pack.

## What this proves

- The HotWire pause-controller layer can fabricate
  `PreChargeRes.EVSEPresentVoltage` and serve it to the PEV before
  the BMS has any opportunity to cross-check against its own inlet
  voltage sensor.
- **The LUXGEN N7's BMS firmware accepted the protocol-reported
  voltage as ground truth**, did not perform an independent physical
  voltage measurement (or did but trusted the EVSE preferentially),
  and proceeded to close its contactors.
- 5 A then flowed continuously from the vehicle pack into a
  controlled external load for the duration of the session — visible
  load behaviour during the capture: a connected light bulb at the
  attacker's side stayed lit (this is the "V2L without operator
  consent" finding documented in ZD-2025-00560).

## Capture metadata

| Field | Value |
|---|---|
| Captured at | 2026-04-27T15:08:55 UTC |
| EVSE side | HotWire on Windows 11 + QCA7005 + custom DC harness |
| PEV side | LUXGEN N7 (production VIN, owner consent, battery >50% SoC) |
| External load | resistive load + indicator bulb on CCS1 DC ±, ~5 A draw |
| Session duration | ~60 minutes(後續 CurrentDemandReq cycles continued at 5 A) |
| Sentinel forged voltage | `220 V` |
| Real HV bus voltage during capture | 0 V on the EVSE side; vehicle pack ~370 V |
| PCAP | `pev/phase4_capture.pcap` (HomePlug AV) |
| Decoded trace | `pev/session.jsonl` (one DIN msg per line) |
| Run config | `pev/config.json` |

## In the paper

Maps to §4 (A2 attack mechanism) and §6 (vehicle field test).
Counterpart paper claim: a HotWire-controlled rogue EVSE can
fabricate the PreCharge voltage reading, and at least one production
EV's BMS will close its contactors based on that fabricated reading,
sourcing energy into an attacker-controlled load.

## Cross-reference

Simulation-mode counterpart (no hardware needed):
`tests/test_attack_sim_mode.py::test_a2_forced_discharge_sim` — sends
sentinel `380 V` over an in-process IPv6 loopback; asserts the PEV
side decodes the forged value. Use this to confirm the *attack
mechanism* end-to-end without a vehicle. The frozen capture above is
what proves the mechanism *works against a real production BMS*.
