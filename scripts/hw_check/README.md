# HotWire hardware-readiness checks

A layered validation suite for bringing up HotWire against real
HomePlug modems, real CCS cabling, and eventually a real EV. Every
phase checks one layer in isolation so when something breaks you
know *where* to look — not just "it doesn't work".

Each run creates a directory under `runs/<timestamp>/` containing:

- `REPORT.md`            Human-readable summary with PASS/FAIL verdicts, metrics, and artifact links
- `session.jsonl`        Every event from every phase, structured
- `phaseN_capture.pcap`  Raw Ethernet / IPv6 capture, one per phase
- `config.json`          The exact flags + host context the run used

Artifacts survive even when a phase crashes — JSONL is flushed after
every event, pcap is written by a separate subprocess, and the report
is re-rendered after each phase completes. You can interrupt with
Ctrl-C and still get a partial report.

## The four phases

| # | Name | What it checks | Needs hardware? |
|---|---|---|---|
| 0 | Environment | Binary, capture tools, interface, capabilities | No |
| 1 | Link | 0x88E1 frames arrive on the interface | Yes — one modem |
| 2 | SLAC | Real-world `CM_SLAC_PARAM`..`CM_SLAC_MATCH` pairing | Yes — two modems (or one modem + charger) |
| 3 | SDP | UDP/IPv6 SECC Discovery request/response | Yes — both modems paired |
| 4 | V2G | Full DIN 70121 session up to CurrentDemand | Yes — full stack |

Later phases implicitly depend on earlier ones. If phase 1 sees zero
frames, running phase 2 will also fail — the suite reports which phase
first broke so you don't chase a symptom.

## Running

### Dev-box dry run (no hardware)

```bash
python scripts/hw_check/run_all.py
```

Phase 0 produces a real result; phases 1-4 report SKIP because no
`--interface` was supplied. Useful to sanity-check the tooling.

### Full PEV-side run against a charger

Works on a Raspberry Pi + a QCA700x-class HomePlug modem wired into
the EV side of the CCS cable. Replace `eth1` with whatever your PLC
modem enumerates as.

```bash
sudo python scripts/hw_check/run_all.py \
    --interface eth1 \
    --role pev \
    --link-duration 15 \
    --slac-budget 25 \
    --v2g-budget 90
```

`sudo` because pcap + raw SLAC frames both require `CAP_NET_RAW`. If
you'd rather not run as root, grant the capability once:

```bash
sudo setcap cap_net_raw,cap_net_admin=eip $(readlink -f $(which python3))
```

### Two modems on a bench (no charger yet)

Two Raspberry Pis, each with one modem, cables bridged. Run each in
its own role:

```bash
# Pi A
sudo python scripts/hw_check/run_all.py -i eth1 --role pev --only 0,1,2

# Pi B
sudo python scripts/hw_check/run_all.py -i eth1 --role evse --only 0,1,2
```

SLAC pairs between Pi A and Pi B, SLAC pcaps are saved on both sides,
and you have a bit-for-bit trail of the exchange.

### Individual phase

Each phase file is a standalone entry point. Useful for tight
iteration on one check:

```bash
sudo python scripts/hw_check/phase1_link.py -i eth1 --duration 30
sudo python scripts/hw_check/phase2_slac.py -i eth1 --role pev --budget 30
sudo python scripts/hw_check/phase3_sdp.py -i eth1 --role pev
sudo python scripts/hw_check/phase4_v2g.py -i eth1 --role pev --budget 120
```

Each standalone invocation creates its own `runs/<timestamp>/`
directory.

## Reading the REPORT.md

The phase summary table at the top gives one-line verdicts:

```
| Phase | Status | Duration | Summary |
|---|---|---|---|
| phase0_env | [PASS] | 0.42s | environment OK |
| phase1_link | [PASS] | 15.30s | 912 frames from 2 MACs — link looks healthy |
| phase2_slac | [PASS] | 3.12s | SLAC paired with 9012a1721f4e (NID=...) |
| phase3_sdp | [PASS] | 1.07s | SECC found at [fe80::1234]:15118 in 0.12s |
| phase4_v2g | [FAIL] | 60.00s | PEV only saw 0/5 CurrentDemandRes messages |
```

For a failure, scroll down to that phase's own section — the `Details`
block has the trailing trace/stages the FSM reached, and the
`Artifacts` section links the pcap you can open in Wireshark.

## When a phase fails

| Phase failing | Likely root cause | Next check |
|---|---|---|
| 0 | Missing binary / tools | Run `python vendor/build_openv2g.py`, install tcpdump |
| 1 | Cable, coupler, modem power | Swap cables; verify with another modem |
| 2 | Wrong NMK, firmware mismatch, or the pair is not on the same AVLN | Inspect `phase2_capture.pcap` for CM_SLAC_PARAM.REQ and check src MAC |
| 3 | IPv6 multicast not reaching the peer, or SECC not on this link | `ping6 ff02::1%eth1` then `tcpdump -i eth1 udp port 15118` |
| 4 | DIN EXI mismatch, TLS expectation, or SECC rejecting SessionSetup | Read `phase4_capture.pcap` with a V2G dissector (e.g. Wireshark with the V2GDecoder plugin) |

## Session log format

Every line in `session.jsonl` is a JSON object with:

```json
{"ts": "2026-04-18T20:52:01.123456+00:00", "kind": "phase.start", "phase": "phase2_slac"}
```

Common `kind` values:

- `run.start`, `run.end`, `run.summary`
- `phase.start`, `phase.end`, `phase.error`
- `pcap.start`, `pcap.stop`, `pcap.skip`
- `phaseN.*` — phase-specific events (`phase2.trace`, `phase3.pev.discovered`, etc.)

The format is stable so you can grep / jq across runs:

```bash
jq 'select(.kind == "phase.end") | {phase, status, duration_s}' \
   runs/20260418-205201/session.jsonl
```

## Safety reminders

- **Do not** run phase 4 against a charger that's servicing another
  customer. The exchange is benign but nothing about the test setup
  is CE-certified; you're a lab rat, not a product.
- **Isolate the high-voltage side of any real CCS cable** before
  plugging in. Phase 1–3 can be run on the low-voltage CP/PE pair
  only; phase 4's proximity pilot behaviour depends on CP being
  pulled correctly. Refer to `doc/hardware.md` (to be written) before
  engaging DC.
