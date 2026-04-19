# Reproducing HotWire — Reviewer path

This is the one-page checklist a reviewer can follow to go from a fresh
clone to a fully-validated HotWire install in ~15 minutes. Every step
below emits artifacts you can inspect independently.

Target host: any Windows 10+ / Linux / macOS box with Python 3.9 or
later. Hardware is **not** required for steps 1–6; the real-hardware
path is documented in step 7.

---

## 0. Prerequisites

- Python 3.9–3.12 (tested on 3.12.4)
- Git with submodule support
- ~500 MB free disk (OpenV2Gx source + Qt runtime)

Optional for step 7 (hardware):

- Raspberry Pi 4 + QCA7005-class HomePlug modem
- CCS Type 1 cable or two back-to-back modems on a bench
- `tcpdump` (Linux) or Npcap (Windows)

---

## 1. Clone with submodules

```bash
git clone --recurse-submodules <repo-url> HotWire
cd HotWire
```

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

---

## 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

Brings in PyQt6, pytest, pytest-qt, pypcap, requests.

---

## 3. (Optional) Rebuild the OpenV2Gx codec from source

This verifies reproducibility: the shipped `hotwire/exi/codec/OpenV2G.exe`
(Windows) or `OpenV2G` (Linux) is a bit-for-bit product of the
vendored source at `vendor/OpenV2Gx/` with the `01-hotwire-custom-params.patch`
applied on top. See `ATTRIBUTION.md` for the LGPL-3.0 compliance note.

```bash
python vendor/build_openv2g.py
```

The script:
1. Applies `vendor/patches/01-hotwire-custom-params.patch`
2. Runs `make` inside `vendor/OpenV2Gx/src`
3. Installs the resulting binary at `hotwire/exi/codec/`
4. Round-trips 22 golden encoder fixtures from
   `tests/_golden_openv2g.json`. Any byte-for-byte difference fails.

Expected output: `OK: 22/22 golden fixtures match`.

---

## 4. Run the full regression suite

```bash
python scripts/run_all_tests.py
```

Expected: **23/23 PASS** (was 19/19 at Checkpoint 12; four new modules
in Checkpoint 13). Each test runs in its own subprocess so one failure
doesn't mask the others.

For per-case detail:

```bash
python -m pytest tests/ -v
```

Key subsuites:

| Subsuite | Command | Pins |
|---|---|---|
| DIN 70121 conformance | `pytest tests/test_din_conformance.py -v` | 29 clause assertions, V2G-DC-* IDs cited in docstrings |
| SLAC real capture replay | `pytest tests/test_homeplug_slac_replay.py -v` | Injects IONIQ6 + Tesla pcapng into EVSE FSM |
| Attacks A1 + A2 | `pytest tests/test_attacks.py tests/test_attack_integration.py tests/test_forced_discharge_integration.py -v` | Override propagation to wire |
| SDP | `pytest tests/test_sdp.py -v` | ff02::1 loopback round-trip |
| Random schema fuzz | `pytest tests/test_random_schema_fuzz.py -v` | 240 random encode+decode pairs |

---

## 5. Run the hardware-readiness dry run

```bash
python scripts/hw_check/run_all.py
```

Without an `--interface`, phase0 PASS and phases 1-4 SKIP. Artifacts
land in `runs/<timestamp>/`:

- `REPORT.md` — human-readable summary
- `session.jsonl` — structured event log
- `config.json` — host context snapshot

Open `REPORT.md` to see the expected row for phase0 (`environment OK`)
and four SKIP rows.

---

## 6. Launch the GUI (two-terminal simulation)

Terminal 1 (EVSE side):

```bash
python scripts/run_gui.py --mode evse --sim
```

Terminal 2 (PEV side):

```bash
python scripts/run_gui.py --mode pev --sim
```

Click **Start** on each. Within ~5 seconds the PEV's trace log reaches
`WaitForCurrentDemandRes` and CurrentDemand messages start flowing.

GUI features to try:

- **Attacks → Launch attack…** → pick Autocharge, type an EVCCID, click
  Apply → the PEV starts sending your spoofed EVCCID
- **File → Open session…** → pick one of the `sessions/*.jsonl` files
  from a previous run → replay panel docks at the bottom → click any
  event → its decoded params populate the tree view
- **Export pcap** from the replay panel → Wireshark-compatible `.pcap`
  with IPv6 + TCP + V2GTP synthesised headers

---

## 7. (Optional) Real-hardware validation

Only run this if you have authorization from the charger operator and
vehicle owner. See [SAFETY.md](../SAFETY.md).

```bash
# PEV side, against a commercial charger
sudo python scripts/hw_check/run_all.py \
    --interface eth1 --role pev \
    --link-duration 15 --slac-budget 25 --v2g-budget 90
```

Replace `eth1` with whatever your PLC modem enumerates as. `sudo`
because pcap and raw SLAC frames require `CAP_NET_RAW`. To grant the
capability persistently:

```bash
sudo setcap cap_net_raw,cap_net_admin=eip $(readlink -f $(which python3))
```

Two-Pi bench (no charger) is covered in `scripts/hw_check/README.md`.

---

## What "PASS" means

- **Automated**: all 23 regression modules return exit code 0. The
  suite has no intentional flakes; any failure is a real regression.
- **Protocol**: messages decoded at the far end match the ones encoded
  at the near end, byte-for-byte, via the OpenV2Gx codec. The 22
  golden encoder fixtures pin this at the wire level.
- **Standards**: DIN TS 70121:2024-11 clauses §8.7.3, §8.7.2, §9.2,
  §9.4, §9.5.3, §9.6, §9.1 are each covered by at least one
  `test_din_conformance.py` case with the V2G-DC requirement ID in the
  docstring.

If a reviewer finds a claim in the paper that isn't mapped above, open
`docs/full_compliance_audit.md` — it's the source of truth for every
testable paper claim.

---

## Where to look when something fails

| Symptom | First check |
|---|---|
| `pip install` fails on PyQt6 | Ensure Python 3.9-3.12; Python 3.13 wheels may lag |
| Codec rebuild byte-mismatch | `git submodule status vendor/OpenV2Gx` to confirm the pinned SHA |
| SDP loopback test hangs | Windows firewall blocking UDP 15118 on loopback; add a rule |
| GUI fails to start | `python -c "from PyQt6 import QtCore; print(QtCore.QT_VERSION_STR)"` |
| hw_check phase 1 reports 0 frames | Wrong interface name; use `python scripts/hw_check/phase1_link.py --interface <x>` to probe |

Full troubleshooting table is in `scripts/hw_check/README.md`.
