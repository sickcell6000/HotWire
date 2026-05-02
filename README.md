# HotWire: Real-World Impersonation and Discharge Attacks on Electric Vehicle Charging Systems

HotWire is a bidirectional V2G charging-protocol testbed for security
research on **DIN 70121** (and ISO 15118-2) DC fast charging. It can
emulate either a rogue **EVSE** (charging station) or a rogue **PEV**
(electric vehicle), and pause / inspect / modify every outbound
message before it hits the wire.

It is the software portion of the WOOT '26 paper of the same name and
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

---

## For AEC reviewers — start here

> 📄 **Full reviewer-facing guide:**
> [`docs/AEC_REVIEWER_GUIDE.md`](docs/AEC_REVIEWER_GUIDE.md) (also
> as PDF in the same folder).
> 🔖 **Zenodo DOI:** [10.5281/zenodo.19986377](https://doi.org/10.5281/zenodo.19986377)
> · **License:** GPL-3.0.
> The section below is the same content
> condensed; the linked guide is what we submit to HotCRP.

**Badges in scope:** Artifacts **Available** + Artifacts **Functional**.

**Not in scope:** Artifacts **Reproduced**. The paper's §6 field-test
results were collected on 4 specific production vehicles and 7
commercial DC fast-charging stations. Re-running those tests requires
physical access to that fleet and is **not** possible from the
artifact alone. Everything else — the FSMs, the attack playbooks, the
GUI, the test suite, the captured traces — is fully exercisable in
software, and that is what this artifact lets you verify.

### Three ways to verify, in 30 minutes

#### 1. One-shot functional check (5 min) — the headline number

##### Linux / Raspberry Pi

```bash
# One-time system prerequisites (Pi/aarch64 has no PyQt5 wheel,
# so we use the apt-packaged Qt5 binary and let venv see it):
sudo apt update
sudo apt install -y python3-venv python3-dev python3-pip python3-pyqt5 python3-pyqt5.qtsvg

git clone --recurse-submodules https://github.com/sickcell6000/HotWire.git
cd HotWire
python3 -m venv --system-site-packages hotwire-venv
source hotwire-venv/bin/activate
pip install -r requirements.txt
bash verify_artifact.sh
```

##### macOS

```bash
git clone --recurse-submodules https://github.com/sickcell6000/HotWire.git
cd HotWire
python3 -m venv hotwire-venv
source hotwire-venv/bin/activate
pip install -r requirements.txt
bash verify_artifact.sh
```

##### Windows

`verify_artifact.sh` is bash-only by design (matches the convention
used by every WOOT 2025 accepted artifact). Confirm you have a bash
environment first — Start menu → search `Git Bash`:

- **Git Bash present** (Git for Windows installed): open Git Bash and
  follow the *Git Bash* block below.
- **Only WSL**: WSL is Linux — follow the **Linux / Raspberry Pi**
  block above, run inside `wsl`.
- **Neither**: install [Git for Windows](https://git-scm.com)
  (~50 MB; bundles Git Bash). Or install WSL.

###### Windows — Git Bash / MSYS2

```bash
# Native Windows Python; venv layout is `Scripts/`, not `bin/`.
git clone --recurse-submodules https://github.com/sickcell6000/HotWire.git
cd HotWire
python -m venv hotwire-venv
source hotwire-venv/Scripts/activate
pip install -r requirements.txt
bash verify_artifact.sh
```

PowerShell users: swap the activate line for
`.\hotwire-venv\Scripts\Activate.ps1`. `verify_artifact.sh` itself
still needs Git Bash or WSL because the script is bash-only.

For real-hardware (PLC modem) work, additionally install
`libpcap-dev` and `pip install -r requirements-hw.txt`.

##### What it runs

The script runs 8 self-contained checks (F0–F6) covering: package
import, unit/integration tests, a 9-cell sim-mode V2G matrix
(3 voltages × 3 durations), the A1 + A2 attack scripts, and
frozen-evidence integrity. Expected output: **`8/8 ✓ ALL PASSED`**.

No hardware required, no internet required after `pip install`,
~5 minutes runtime, Python 3.11+. **Docker is *optional*** —
`verify_artifact.sh` F1 uses the Docker CI path if a Docker daemon
is running (collects 278 tests inside the container), and
transparently falls back to the host pytest path if not (collects
179 tests, 99 are Docker-only and auto-skipped). Either path
produces F1 = PASS and the artifact reaches `8 / 8 ✓ ALL PASSED`.

#### 2. Interactive GUI demo (10 min) — see the attacks live

In two terminals:

```bash
# Terminal 1 — rogue charging station
python scripts/run_gui.py --mode evse --sim

# Terminal 2 — rogue vehicle
python scripts/run_gui.py --mode pev --sim
```

Both processes loop back over a virtual PLC link (no Ethernet needed).
The GUI shows every DIN 70121 message in a four-tab tree (Combined /
Rx / Tx / Trace log) and lets you pause-and-override fields before
they go on the wire. Try the bundled attack presets in
`config/attack_presets.json` — they reproduce A1 (EVCCID
impersonation) and A2 (forged PreCharge voltage) end-to-end.

#### 3. Inspect frozen real-hardware evidence (15 min) — the paper's data

```
datasets/real_hw_traces/comprehensive_matrix/
├── test1_a1_evccid/            ← A1 attack capture
├── test2_a2_voltage/           ← A2 attack capture
├── test3_multi_pause/ … test11_long_running/
├── README.md                   ← per-test rationale
└── FINDINGS.md                 ← what each capture demonstrates
```

Each test bundle contains a `*.pcap` (open in Wireshark with the
HomePlug AV dissector), a `session.jsonl` (decoded V2G messages with
`values:` field showing actual EVCCID / voltages / SoC / etc.), and a
`config.json` recording the run parameters. No EXI decoder needed to
read the JSONL — the recorded fields are already plain JSON.

A short reviewer-walk through each bundle is in
[`docs/PAPER_VALIDATION.md`](docs/PAPER_VALIDATION.md).

### What this maps to in the paper

| Paper claim                                | Where to verify                                      |
|--------------------------------------------|------------------------------------------------------|
| §3 DIN 70121 / ISO 15118-2 FSM             | `verify_artifact.sh` F1+F2+F3 / `hotwire/fsm/`       |
| §4 A1 EVCCID-impersonation attack          | F4 / GUI demo / `test1_a1_evccid/`                   |
| §4 A2 forced-discharge attack              | F4 / GUI demo / `test2_a2_voltage/`                  |
| §5 Tooling (FSM, GUI, pause/override)      | F0–F2 / GUI demo                                     |
| §6 Field results on real fleet             | **Not reproducible without hardware** — frozen pcaps |

### If something fails

Open an AEC review issue with the failing line from
`verify_artifact.sh` and the OS / Python version. Known-good envs:
Ubuntu 22.04 + Python 3.11, Windows 11 + Python 3.12, Raspberry Pi OS
(bookworm) + Python 3.11.

---

## Quick start

The same install + verify commands shown in
[*For AEC reviewers — start here*](#for-aec-reviewers--start-here)
above. The deeper Artifact-Evaluation walkthrough lives in
[the docs/ AE guide](docs/); the test-suite design is in
[docs/PAPER_VALIDATION.md](docs/PAPER_VALIDATION.md).

## First-time setup on a real bench

The OpenV2G EXI codec is platform- and architecture-specific
(Linux/x86_64 ≠ Linux/aarch64 ≠ Windows/x86_64), so we don't track
pre-built binaries in git. Build it once on every host:

```bash
# Linux / macOS / Raspberry Pi (aarch64)
bash scripts/build_codec.sh

# Windows (PowerShell, MSYS2 + MinGW-w64 required)
.\scripts\build_codec.ps1
```

The build runs `make` in `vendor/OpenV2Gx/Release/` and copies the
result to `hotwire/exi/codec/OpenV2G` (POSIX) or `OpenV2G.exe`
(Windows). Re-run after pulling submodule updates.

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
docs/PAPER_VALIDATION.md    6-suite test runbook (paper §5/§6 mapping)
verify_artifact.sh          AEC functional-badge one-shot (~5 min)
```

## Lineage and license

HotWire builds on [pyPLC](https://github.com/uhi22/pyPLC) (GPL-3.0,
Uwe Hinrichs) for the DIN 70121 / SLAC reference and on
[OpenV2Gx](https://github.com/uhi22/OpenV2Gx) (LGPL-3.0, Siemens AG)
for the EXI codec. HotWire itself is **GPL-3.0**.

See the [vendor/ attribution](vendor/) for the full per-file lineage.

## Reporting vulnerabilities

See [SECURITY.md](SECURITY.md). HotWire follows coordinated disclosure
on every finding the paper makes against named vendors.
