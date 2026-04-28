# HotWire — DIN 70121 / ISO 15118-2 Charging Security Testbed

HotWire is a bidirectional V2G charging-protocol testbed for security
research on **DIN 70121** (and ISO 15118-2) DC fast charging. It can
emulate either a rogue **EVSE** (charging station) or a rogue **PEV**
(electric vehicle), and pause / inspect / modify every outbound
message before it hits the wire.

It is the software portion of the WOOT '26 paper *"HotWire: Real-World
Impersonation and Discharge Attacks on EV Charging Systems"* and
implements the two attacks described there:

- **A1 — Unauthorized Autocharge** — replay a captured victim
  `EVCCID` against an Autocharge-enabled station to bill the session
  to the victim's account.
- **A2 — Unauthorized Energy Extraction** — forge `PreChargeRes`
  voltage so a vulnerable BMS closes its high-voltage contactors into
  an attacker-controlled load.

> **Security research use only.** Written authorization from both the
> vehicle owner and the charging operator is required before running
> against real hardware. See [SAFETY.md](SAFETY.md).

## Quick start (no hardware)

```bash
git clone --recurse-submodules <repo-url> HotWire
cd HotWire
docker compose run --rm hotwire-ci      # 240 tests, ~64 s, exit 0
```

For the full Artifact-Evaluation walkthrough see [ARTIFACT.md](ARTIFACT.md).

## What's in the repo

```
hotwire/                  Python package (FSM, PLC, SDP, attacks, GUI)
├── fsm/                    DIN 70121 state machines
├── plc/                    HomePlug AV / pcap / simulation transport
├── sdp/                    SECC Discovery Protocol
├── core/                   HotWireWorker, address manager, config
└── attacks/                A1 + A2 playbooks   ← security-relevant

scripts/hw_check/           phase0-10 bench runners (sim + real-hw)
tests/                      283 pytest cases (240 inside Docker)
datasets/real_hw_traces/    curated pcap + jsonl from a real Pi/Win bench
docs/hardware_design_guide.md   537-line operator-grade build guide
verify_artifact.sh          AEC functional-badge one-shot (~5 min)
```

## Lineage and license

HotWire builds on [pyPLC](https://github.com/uhi22/pyPLC) (GPL-3.0,
Uwe Hinrichs) for the DIN 70121 / SLAC reference and on
[OpenV2Gx](https://github.com/uhi22/OpenV2Gx) (LGPL-3.0, Siemens AG)
for the EXI codec. HotWire itself is **GPL-3.0**.

See [ATTRIBUTION.md](ATTRIBUTION.md) for the full per-file lineage.

## Reporting vulnerabilities

See [SECURITY.md](SECURITY.md). HotWire follows coordinated disclosure
on every finding the paper makes against named vendors.
