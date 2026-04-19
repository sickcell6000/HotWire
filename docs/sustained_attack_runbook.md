# Sustained Attack A2 — reproduction runbook

This document describes how to reproduce the paper's §6 "sustained
60-minute forced discharge" claim and the linear extrapolation to an
8-hour overnight session, using the HotWire toolkit plus the resistive
load bank described in §5 / `hardware/schematics/wiring_diagram.md`.

The runbook assumes:

- You have assembled the EVSE emulator per §5 and validated it with a
  bench-only Scenario C run (`scripts/hw_check/run_all.py --role evse`)
- The target vehicle is **yours or one for which you have explicit
  written authorization from the owner** (see `SAFETY.md` and §10
  Ethics Considerations)
- A 1.25 kW resistive load is wired to DC+ and DC− of the CCS cable
  with an optocoupler-isolated emergency disconnect under software
  control (per the schematic's Arduino sense board)
- The vehicle is parked with parking brake engaged, in a covered
  space away from public thoroughfares

**Read `SAFETY.md` in full before proceeding. Improper wiring at the
DC pins can kill.**

---

## Artifacts the runbook produces

After the run completes, `runs/<timestamp>/` contains:

| File | What it proves |
|------|----------------|
| `REPORT.md` | One-page summary + PASS/FAIL verdict |
| `session.jsonl` | Every decoded DIN 70121 Req/Res the worker observed |
| `sustained.jsonl` | Periodic checkpoint (once per `--sample-interval`) with CurrentDemand counters + fabricated voltage/current + last observed `EVTargetCurrent` |
| `phase4_capture.pcap` | Full bit-for-bit wire capture from `tcpdump`/`dumpcap` |
| `config.json` | Host context snapshot (interface, clock, OS, Python version) |

A reviewer can open `phase4_capture.pcap` in Wireshark + dsV2Gshark and
**visually confirm** that every `CurrentDemandRes` across the full
duration carries the fabricated `EVSEPresentVoltage` and
`EVSEPresentCurrent` — i.e. the deception is sustained, not just
present at handshake.

---

## One-command reproduction (60-minute session)

```bash
# 1. On the Raspberry Pi with the EVSE emulator. eth1 is the PLC modem.
sudo python scripts/record_sustained.py \
    --interface eth1 \
    --duration 3600 \
    --voltage 380 \
    --current 10 \
    --sample-interval 60
```

Expected stdout:

```
=== Attack: A2 — Unauthorized Energy Extraction ===
  Mode: EVSE
  …
  Overrides:
    PreChargeRes:
      EVSEPresentVoltage = 380
    CurrentDemandRes:
      EVSEPresentVoltage = 380
      EVSEPresentCurrent = 10
[ok] pcap: runs/20260421-HHMMSS/phase4_capture.pcap
[run] attack active for 3600s (checkpoint every 60s)
  [    60.0s] CD sent=198   seen=198   target_I=125
  [   120.0s] CD sent=396   seen=396   target_I=125
  …
  [  3600.0s] CD sent=11856 seen=11856 target_I=125
============================================================
Sustained Attack A2 run for 3600.0s (target 3600s).
CD Req seen: 11856, CD Res sent: 11856.
Fabricated V=380V, I=10A held throughout.
============================================================
Artifacts under: runs/20260421-HHMMSS
```

**Interpreting the output:**

- `CD sent` should grow at ~3.3 messages/second (typical CurrentDemand
  cycle is 300 ms per DIN 70121 Table 76)
- `CD seen` should track `CD sent` (1:1 Req↔Res)
- The same fabricated `380V / 10A` is present in every one of those
  ~12,000 `CurrentDemandRes` messages across 60 minutes

---

## Overnight reproduction (8-hour session, direct measurement)

The paper's 10 kWh / 25%-of-a-40-kWh figure is currently a linear
extrapolation from the 60-minute datum. To produce an overnight
datapoint directly:

```bash
sudo python scripts/record_sustained.py \
    --interface eth1 \
    --duration 28800 \
    --voltage 380 \
    --current 10 \
    --sample-interval 300
```

Notes:

- `--sample-interval 300` (5 min) keeps `sustained.jsonl` small
  (~100 lines across 8 hours) while still giving a tight grid to
  confirm the BMS didn't autonomously open contactors
- The pcap will be large (~100–300 MB depending on CurrentDemand
  cadence); provision disk accordingly
- If the target BMS has a safety timer that closes its side of the
  contactor after N minutes, the sustained log's last checkpoint
  before `CD seen` stops incrementing gives you that timer's duration
  precisely

Before running overnight, verify the 60-minute run worked end-to-end
on the same vehicle — a ≥1-hour run is prerequisite for the overnight
datum to be meaningful.

---

## Post-run checks

After the run, verify the claim survives scrutiny:

```bash
# 1. Confirm every CurrentDemandRes really carries the fabricated voltage.
#    Filter to `tx` direction, msg_name=CurrentDemandRes, count how many
#    have EVSEPresentVoltage.Value = 380.
jq -r 'select(.direction == "tx" and .msg_name == "CurrentDemandRes")
       | .params."EVSEPresentVoltage.Value"' \
   runs/<ts>/session.jsonl | sort | uniq -c
# Expected output:
#    <N>  380
# (If another value appears, the override dropped mid-session — investigate.)

# 2. Confirm the periodic sample log aligns with pcap timestamps.
jq '.elapsed_s, .cd_res_sent' runs/<ts>/sustained.jsonl | head -20

# 3. Open the pcap in Wireshark.
wireshark runs/<ts>/phase4_capture.pcap
# Filter: v2gtp && ip6
# Install dsV2Gshark for DIN 70121 dissection:
#   https://github.com/dSPACE-group/dsV2Gshark
```

---

## Anonymising before release

`sessions/` and `runs/` contain real EVCCID / EVSEID / SessionID
values. Before distributing any artifact to reviewers or the public:

```bash
# 1. Redact the JSONL
python scripts/redact_session.py runs/<ts>/session.jsonl \
    --out runs/<ts>/session.redacted.jsonl

# 2. Re-export as pcap so the pcap matches the redacted JSONL
python scripts/export_pcap.py runs/<ts>/session.redacted.jsonl \
    --out runs/<ts>/session.redacted.pcap
```

See `docs/dataset.md` for the anonymisation contract.

---

## Failure modes + first-response

| Symptom | First-response diagnosis |
|---|---|
| `worker construction failed` | Interface name wrong or modem not up. Run `python scripts/hw_check/phase0_hw.py -i eth1` for the full 21-check preflight. |
| `CD sent` stays at 0 for > 30 seconds | BMS refused to close contactors (Tesla-like behaviour). Expected for some vehicles — compare to `runs/<ts>/phase4_capture.pcap` to confirm the fault indicator came from the EV. |
| `CD sent == 0` but `CD seen > 0` | Worker got the Req but didn't reply — codec or override problem. Check `session.jsonl` for `phase4.worker_error` events. |
| `CD seen` stops incrementing mid-session | BMS hit an internal safety timer. **This is a real and reportable observation** — record the elapsed time and include it in the paper's discussion. |
| pcap missing | `tcpdump`/`dumpcap` not on PATH or insufficient privileges. Run with `sudo` or grant capability: `sudo setcap cap_net_raw,cap_net_admin=eip $(readlink -f $(which python3))` |

---

## Safety interlocks this runbook depends on

The paper's Ethics Considerations (§10) lists four safety layers. The
runbook only works when all four are present in the physical setup:

1. **Emergency contactor** (Arduino D2 → optocoupler relay → DC+ cutoff).
   Triggered automatically when `|I| > 60 A` measured by ACS758.
2. **Current limiting** inherent to the 1.25 kW resistive bank at
   battery voltage (e.g. 380 V / 1.25 kW = ~3.3 A → well under the
   60 A interlock threshold).
3. **Automated session termination** at a chosen SOC drop. This runbook
   defaults to 60-minute wall-clock; for a longer run, pair with the
   vehicle's own SOC display (manual) or an OBD-II SOC query (not
   implemented in HotWire — you'd read it externally).
4. **Parking brake + high-voltage PPE** worn by operators.

If any of the four are absent, **do not run** — the protocol-layer
attack is unchanged but the physical-layer safety is not.

---

## Related artifacts

- `hotwire/attacks/forced_discharge.py` — the Attack A2 playbook
  (overrides both `PreChargeRes` and `CurrentDemandRes`)
- `hotwire/attacks/base.py` — the `Attack` dataclass contract
- `tests/test_forced_discharge_integration.py` — the simulation-level
  regression that ensures the override reaches the wire (runs in
  every commit)
- `docs/attacks.md` — user-facing attack documentation
- `hardware/schematics/wiring_diagram.md` — the physical setup the
  runbook assumes
- `SAFETY.md` — the pre-energisation checklist
