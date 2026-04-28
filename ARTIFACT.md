# HotWire — Artifact Evaluation Guide

> Entry-point for the **USENIX WOOT '26 Artifact Evaluation Committee**.
> Two badges requested: **Artifacts Available** + **Artifacts Functional**.

---

## ⚠️ Malicious-operations notice

HotWire packages two live attacks on CCS DIN 70121 / ISO 15118-2:

1. **A1 — Autocharge EVCCID impersonation** — crafts `SessionSetupReq`
   with a victim EVCCID; an Autocharge-enabled station bills the
   victim's account. Code: `hotwire/attacks/autocharge_impersonation.py`.
2. **A2 — Forced discharge** — crafts `PreChargeRes` /
   `CurrentDemandRes` with fabricated voltage; a vulnerable BMS closes
   contactors into the attacker's load. Code:
   `hotwire/attacks/forced_discharge.py`.

> **Both badges can be evaluated entirely inside Docker + simulation
> loopback. No charging station, no vehicle, no PLC modem required.**
> The verification path emits no malicious frame onto any real network.

Operator authorization + electrical safety: see [SAFETY.md](SAFETY.md).

---

## 1. Artifacts Available

The repository is GPL-3.0 and archived on Zenodo at the DOI printed on
the artifact appendix PDF (`docs/artifact_abstract.pdf`).

The Zenodo record contains:

- `HotWire-<git-sha>.zip` — full source tarball
- `hotwire-ci.tar.gz` — pre-built Docker image (optional, saves
  ~20 min build time)
- `artifact_abstract.pdf` — this evaluation guide's companion appendix

That's it; Available is a one-click confirmation once the DOI resolves.

---

## 2. Artifacts Functional

### 2.1 One-command verification

```bash
./verify_artifact.sh                # macOS / Linux / WSL
bash verify_artifact.sh             # Windows Git-Bash / PowerShell
```

Runtime: **~5 minutes** with `hotwire-ci.tar.gz` pre-loaded;
**~25 minutes** building from source. Final line on success:

```
[verify_artifact] ✓ ALL FUNCTIONAL CHECKS PASSED
```

### 2.2 What the script verifies

| Check | Claim | Expected output |
|---|---|---|
| **F0** — Codec presence | OpenV2G binary exists or auto-builds from `vendor/` | `OK: 22/22 golden fixtures match` |
| **F1** — Docker CI regression | All registered tests pass | `240 passed, 6 skipped, exit 0` (~64 s) |
| **F2** — Sim full DIN 70121 session | PEV walks all 13 states on `::1` loopback | ≥ 5 `CurrentDemandReq` observed |
| **F3** — Parametric matrix | Voltage × duration matrix all PASS | 9/9 rows marked `PASS` |
| **F4** — Attack-code presence | A1 + A2 source compiles | both modules exit `py_compile` clean |
| **F5** — Sim-mode attack reach | A1 + A2 fabricated values reach the wire | 3/3 sim-attack tests PASS |
| **F6** — Real-hardware evidence | `datasets/real_hw_traces/` pcap bundles intact | all bundle pcaps validate as pcap v2.4 |

### 2.3 Manual run of individual checks

```bash
docker compose run --rm hotwire-ci                    # F1
./scripts/sim_loopback.sh 25                          # F2
./scripts/sim_matrix.sh                               # F3
python -m pytest tests/test_attack_sim_mode.py -v     # F5
```

### 2.4 Test counts (one number, three contexts)

| Context | Count | What it means |
|---|---:|---|
| Full `pytest tests/` (PyQt6 present) | **283** | Every test the repo ships |
| Docker CI (this is what F1 reports) | **240** | The Docker image registers 240 tests + 6 skips for live-hardware tests that can't run in a container |
| Bare Python without PyQt6 | **169** | If reviewer runs `pytest` on a host with no PyQt6, 71 GUI tests skip cleanly |

The Functional badge passes if **F1** reports `240 passed`. The
non-Docker pathways (283 / 169) are listed only so reviewers don't
get confused if they grep for test counts manually.

---

## 3. Dependencies

### Recommended (covers all of F0–F6)

- Docker 24+ with Compose v2
- ~10 GB free disk

### Fallback without Docker

- Python 3.9–3.12
- `pip install -r requirements.txt`
- `libpcap` / Npcap is **not** required for F1–F6; sim mode uses `::1`

### Not required

- PLC modem hardware (QCA7420, TP-Link PA4010)
- Any vehicle or charging station
- A CCS cable, transformers, or Arduino board

If you want to *understand* the hardware path used to capture the
bundles in `datasets/real_hw_traces/` see
[`docs/hardware_design_guide.md`](docs/hardware_design_guide.md). The
Functional badge does not depend on any of it.

---

## 4. Repository layout

```
hotwire/                  Python package
├── fsm/                    DIN 70121 state machines
├── plc/                    HomePlug AV / pcap / sim transport
├── sdp/                    SECC Discovery Protocol
├── core/                   HotWireWorker, address manager, config
├── attacks/                A1 + A2 playbooks   ← security-relevant
├── exi/                    OpenV2G EXI codec wrapper
└── gui/                    PyQt6 GUI (not needed for AEC)

scripts/hw_check/           phase 0–10 bench runners
tests/                      283 pytest cases (240 in Docker CI)
docker-compose.yml          CI orchestration
Dockerfile                  Multi-stage codec-builder + runtime
verify_artifact.sh          F0–F6 one-shot AEC harness

datasets/real_hw_traces/    Curated real-hardware evidence (§5)
docs/hardware_design_guide.md   Operator-grade build guide

ARTIFACT.md                 ← you are here
SAFETY.md                   Authorization + electrical safety
ATTRIBUTION.md              Per-file lineage / GPL-3 compliance
```

---

## 5. Real-hardware evidence (optional reading)

Although the badge path is simulation-only, the repo ships **11
curated bundles** of real-hardware captures from a Raspberry Pi 4 PEV
↔ Windows EVSE bench (QCA7420 HomePlug AV modems). Reviewers can
open the pcap files in Wireshark + the
[dsV2Gshark](https://github.com/SecureV2X/dsV2Gshark) plugin and
confirm the paper's framework-level claims without owning hardware.

Layout:

```
datasets/real_hw_traces/
├── phase4_clean_pass/       Full DIN 70121 9-stage V2G session
├── phase4_a2_attack/        ForcedDischarge fabricates 380 V
├── phase5_pause_send/       Sentinel 777 V observed at peer
└── comprehensive_matrix/
    ├── test1_a1_evccid          spoofed EVCCID round-trips
    ├── test2_a2_voltage         A2 at 220 V, 3 CurrentDemandRes
    ├── test3_multi_pause        two stages paused simultaneously
    ├── test4_abort              FSM continues with originals
    ├── test5_prepair_gate       env-var gate behaves
    ├── test6_pev_override       PEV-side override symmetric
    ├── test9_fuzz/round{1..5}   random EVCCID/V/I, 5/5 reach wire
    ├── test10_stress_postfix    worker reuse 5/5 PASS
    ├── test11_long_running      180 s, RSS +0.0 %, 1891 CD msgs
    ├── README.md                per-test PASS criteria + timing
    └── FINDINGS.md              bugs the matrix surfaced + fixes
```

Each bundle contains a Pi-side pcap and a `session.jsonl` log keyed by
message stage.

`FINDINGS.md` is worth reading: it documents seven bugs the test
matrix surfaced during artifact preparation and the patches that
landed for each — including the worker-shutdown TCP-server-leak that
took the stress test from 1/10 to 5/5 PASS.

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Docker daemon unreachable` | Start Docker Desktop; wait for `Engine running`; verify with `docker info` |
| F1 says `169 passed` instead of 240 | PyQt6 missing in the Python env; OK — see Test-counts table above; install with `pip install PyQt6 pytest-qt` |
| F0 reports `Exec format error` | x86_64 codec on aarch64 host; `vendor/build_openv2g.py` rebuilds from source |
| `pcap import error` | F1–F6 don't use pcap; only `scripts/hw_check/phase1_link.py` does, which is out of badge scope |

For anything else: contact the AEC chair (`woot26aec@usenix.org`).
WOOT rules require author–AEC communication through the chair.

---

## 7. Known limitations

- **Paper §6 Real-World Evaluation is scope-out.** A1 against
  7 commercial networks and A2 against 4 production EVs (Tesla,
  Luxgen, CMC, Hyundai) require infrastructure that cannot be
  packaged. The Functional badge confirms framework capability only.
- **Live-hardware AEC re-run is not requested.** Real-hardware results
  are shipped as pcaps + JSONL under `datasets/real_hw_traces/` so
  reviewers can inspect without owning hardware.
- **Docker image size.** Prebuilt `hotwire-ci.tar.gz` is ~800 MB. If
  Zenodo size is a concern, build from source: the Dockerfile is
  fully reproducible.
