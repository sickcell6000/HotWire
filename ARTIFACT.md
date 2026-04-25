# HotWire — Artifact Evaluation Guide

> Entry-point for the **USENIX WOOT '26 Artifact Evaluation Committee (AEC)**.
> Start here. This file is written specifically to support the two badges
> we request: **Artifacts Available** and **Artifacts Functional**.

---

## ⚠️ Malicious-operations notice (required by the AEC call)

HotWire is an **offensive security research framework** for CCS DIN 70121 /
ISO 15118-2. Two live attacks are packaged:

1. **A1 — Autocharge EVCCID impersonation** (`hotwire/attacks/autocharge_impersonation.py`)
   — crafts a CCS `SessionSetupReq` carrying a victim EVCCID such that a
   production charging station authorizes the session under the victim's
   billing account. If executed against real public charging infrastructure,
   this produces **unauthorized financial transactions**.

2. **A2 — Forced discharge via fabricated PreChargeRes** (`hotwire/attacks/forced_discharge.py`)
   — crafts `PreChargeRes` / `CurrentDemandRes` messages containing fabricated
   voltage readings so that a vulnerable BMS closes its high-voltage
   contactors against a 0 V external rail. If executed against a real vehicle
   without a safety load bank and current-limiting interlocks, this can
   result in **uncontrolled battery discharge**.

> **Every item in this artifact can be evaluated for Available + Functional
> badges entirely inside Docker + simulation loopback — no real charging
> station, no real vehicle, and no PLC modem are required.** The evaluation
> paths documented below do not emit any malicious frame onto any real
> network.

For full safety/authorization notes see [`SAFETY.md`](SAFETY.md). For our
IRB approval and responsible-disclosure evidence see
[`docs/ethics_evidence.md`](docs/ethics_evidence.md).

---

## Requested badges

| Badge | Status in this submission | Verification path |
|---|---|---|
| **Artifacts Available** | ✅ Requested | Repository released under GPL-3.0 at Zenodo DOI (see §1) |
| **Artifacts Functional** | ✅ Requested | `./verify_artifact.sh` runs the full functional check in ~5 minutes; §2 details |
| Results Reproduced | ❌ **Not** requested | Paper §6 Real-World Evaluation results depend on production charging infrastructure and specific OEM vehicles under NDA; AEC cannot reproduce these. Framework-level results in §5 are however covered by the Functional badge path. |

---

## 1. Artifacts Available verification

**What the AEC checks**: the artifact is publicly available at a stable URL.

**How to verify**:

1. Open the Zenodo record at <https://doi.org/10.5281/zenodo.19754105>.
2. The record contains:
   - `HotWire-<git-sha>.zip` — complete source tarball.
   - `hotwire-ci.tar.gz` — pre-built Docker image (optional, saves ~20 min build time).
   - `ARTIFACT.md`, `SAFETY.md`, `README.md` (this guide + safety + user docs).
   - `paper.pdf`, `artifact_abstract.pdf`.
   - `expected_outputs/` — reference logs from our own runs for comparison.
3. The repository is released under **GNU GPL-3.0** (see `LICENSE`).

That's it; Available badge is a one-click confirmation.

---

## 2. Artifacts Functional verification

**What the AEC checks**: the core functionality of the artifact can be
confirmed by the AEC.

We define "core functionality" as the four claims below; the `verify_artifact.sh`
script automates the check for all four.

### 2.1 One-command verification (recommended)

```bash
# Unix / macOS / WSL
./verify_artifact.sh

# Windows PowerShell — use the same shell script via WSL or Git Bash
bash verify_artifact.sh
```

Expected total runtime: **~5 minutes** if the Docker image is pre-loaded
from `hotwire-ci.tar.gz`; **~25 minutes** if building from source.

Expected final line: `[verify_artifact] ✓ ALL FUNCTIONAL CHECKS PASSED`.

### 2.2 What the script actually verifies

| Check | Claim | Expected output |
|---|---|---|
| **F1** — Docker CI regression | "240 unit + integration tests pass" | `240 passed, 6 skipped` + `Overall exit code: 0` |
| **F2** — Simulation-mode full DIN 70121 session | "HotWire walks 13 PEV states from SessionSetup to CurrentDemand on ::1 loopback" | `entering 14:WaitForCurrentDemandRes` observed, ≥5 CurrentDemandReq msgs |
| **F3** — Parametric matrix (9 runs) | "Voltage × duration matrix all PASS" | All 9 rows marked `PASS` |
| **F4** — Attack code presence | "A1 + A2 attack logic is in the repo" | `hotwire/attacks/autocharge_impersonation.py` + `hotwire/attacks/forced_discharge.py` exist and are syntactically valid Python |

### 2.3 Manual checks (if `verify_artifact.sh` fails)

Each of F1–F4 can also be run individually:

```bash
# F1 — Docker CI (runs 240-test regression; builds image on first run)
docker compose run --rm hotwire-ci

# F2 — One-command sim V2G session (takes 25s)
./scripts/sim_loopback.sh 25

# F3 — Parametric matrix (takes ~5 min; 9 combinations of voltage × duration)
./scripts/sim_matrix.sh

# F4 — Attack code sanity
python3 -m py_compile hotwire/attacks/autocharge_impersonation.py
python3 -m py_compile hotwire/attacks/forced_discharge.py
```

### 2.4 Reference output

If you need to compare your output against ours, `expected_outputs/` in the
Zenodo bundle contains:

- `docker_ci_expected.log` — our run of `docker compose run --rm hotwire-ci`
- `sim_loopback_expected.log` — our run of `sim_loopback.sh 25`
- `sim_matrix_expected.txt` — our matrix table

Small numeric differences (CurrentDemand message counts, `startup_ms` values)
are expected — your host's clock resolution, CPU speed, and Python import
cache behavior all introduce variance. The **shape** of the output —
states reached, message types exchanged, result column — should match ours
exactly.

---

## 3. Dependencies and host requirements

### Minimum for Functional badge (recommended)

- **Docker** 24+ with Compose v2 (Docker Desktop or native Linux daemon)
- ~10 GB disk space (for the Docker image + coverage artifacts)
- No special kernel features, no network hardware beyond IPv6 loopback

### Fallback without Docker

- **Python 3.9 – 3.12** (tested on 3.11 + 3.12)
- `pip install -r requirements.txt`
- A pcap library — `libpcap` on Linux, Npcap on Windows — only needed if you
  want to run the hardware-level phases (`scripts/hw_check/phase*_*.py`),
  not for the F1–F4 checks above
- The Functional-badge path runs fine without any pcap stack on a plain
  Python install; it uses IPv6 loopback `::1`.

### Not required for this artifact evaluation

- PLC modem hardware (QCA7420, TP-Link PA4010, Codico modules)
- Any vehicle or charging station
- Npcap / pypcap on Windows
- A CCS cable, coupling transformers, or Arduino boards

If you want to explore these for curiosity, see
[`docs/hardware_design_guide.md`](docs/hardware_design_guide.md) — 537
lines of BOM, schematics, recovery procedures. But the Functional badge
path does not use any of it.

---

## 4. Repository structure (what to look at)

```
hotwire/                  Main Python package
├── fsm/                    DIN 70121 state machines (fsm_pev.py 12 stages,
│                           fsm_evse.py 12 stages)
├── plc/                    HomePlug / PCAP / simulation transport
├── sdp/                    SDP client + server (IPv6 scope-aware)
├── core/                   HotWireWorker, address manager, config
├── attacks/                A1 + A2 attack playbooks        ← security-relevant
├── exi/                    OpenV2G EXI codec wrapper + fixtures
└── gui/                    PyQt6 GUI (not needed for AEC)

scripts/
├── hw_check/               Phase 0-4 bench scripts (hardware-aware)
├── run_evse.py  run_pev.py One-process simulation entry points
├── sim_loopback.sh         Two-process full V2G session         ← F2
├── sim_matrix.sh           Voltage×duration parametric matrix   ← F3
├── sim_protocol_matrix.sh  DIN/ISO/Tesla protocol variants
└── sim_stress_matrix.sh    Back-to-back session leak test

tests/                     169 pytest tests (240 inside Docker with PyQt6)
docker-compose.yml         CI orchestration (hotwire-ci service)   ← F1
Dockerfile                 Multi-stage: codec-builder + runtime

docs/
├── REPRODUCING.md          Fresh-clone to validated install walkthrough
├── hardware_design_guide.md  Modem modification + recovery (537 lines)
├── ethics_evidence.md      Paper §10 claim → artifact evidence map
├── paper_compliance.md     Paper claim-by-claim compliance audit
└── parametric_test_matrix_2026-04-23.md  32-run test matrix report

patches/pyplc/             Patches required to run upstream pyPLC on Windows
SAFETY.md                  Authorization + electrical safety (hardware use)
ARTIFACT.md                ← You are here
LICENSE                    GPL-3.0
```

---

## 5. Troubleshooting

### "Docker daemon unreachable"

On Windows, open Docker Desktop → wait for `Engine running`. Verify via:
```bash
docker info | grep Server:
```
If `Server:` is missing, the engine isn't up yet.

### "`.venv` missing" or "pip-installed packages not found"

The AEC path uses Docker, not a host venv. If for some reason you must run
outside Docker:
```bash
python3 -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows
pip install -r requirements.txt
```

### "`pcap` import error on Linux"

The F2 Functional check doesn't use pcap (sim mode uses `::1` loopback).
If you're trying to run `scripts/hw_check/phase1_link.py` without pcap,
that's expected and out of scope for this evaluation.

### "240 passed" becomes "169 passed"

This indicates Python can't import PyQt6 in your environment. The
PyQt6-backed GUI tests contribute 71 of the 240 tests. They skip cleanly
if PyQt6 is absent. Only Docker installs PyQt6 automatically; on bare
Python you'd need `pip install PyQt6 pytest-qt`.

This is not a failure for the Functional badge, as long as the 169
non-GUI tests pass. The script labels this as `WARN: PyQt6 unavailable`
and continues.

### Anything else

Contact the authors via the AEC chair. The WOOT '26 rules explicitly
allow author–AEC communication through the chair to resolve install
glitches while preserving reviewer anonymity.

---

## 6. Known limitations (important context for the AEC)

- **No real-hardware test in this artifact.** The artifact can be fully
  evaluated in Docker + simulation loopback. The hardware-mode SLAC /
  SDP passes reported in the paper (§5) were captured on a specific
  bench rig; we provide the pcaps and logs for AEC private review on
  request but do not attempt to have the AEC reproduce them.

- **Paper §6 Real-World Evaluation results are scope-out.** The A1 attack
  results against "7 commercial charging networks" and A2 results on 4
  production EVs (Tesla, Luxgen, CMC, Hyundai) depend on infrastructure
  we cannot package. For the Functional badge we ask the AEC to confirm
  only the framework capability, not the real-world success rates.

- **Docker image size.** The prebuilt `hotwire-ci.tar.gz` is ~800 MB
  due to PyQt6 and the OpenV2G codec toolchain. If Zenodo upload size
  is a concern, the AEC can skip the tarball and build from source; the
  Dockerfile is reproducible and documented.

---

## 7. Contacts

Per WOOT '26 rules, all author–AEC communication goes through the chair
(`woot26aec@usenix.org`). Please use that address if `verify_artifact.sh`
fails or any of the paths in this guide do not behave as documented.
