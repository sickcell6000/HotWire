# HotWire — DIN 70121 / ISO 15118-2 Charging Security Testbed

HotWire is a bidirectional V2G charging protocol testbed for security research
on **DIN 70121** (and ISO 15118-2) Electric Vehicle charging. It can emulate
either side — a rogue **EVSE** (charging station) or a rogue **PEV** (electric
vehicle) — and pause, inspect, and modify every outbound message before it
hits the wire.

HotWire is the software portion of the research described in our paper
*"HotWire: Real-World Impersonation and Discharge Attacks on Electric Vehicle
Charging Systems"* and supports the two attacks documented there:

- **A1 — Unauthorized Autocharge:** capture a victim vehicle's `EVCCID` and
  replay it at an Autocharge-enabled station to bill to the victim's account.
- **A2 — Unauthorized Energy Extraction:** forge `PreChargeRes` voltage
  claims to trick a vulnerable BMS into closing its high-voltage contactors
  into an attacker-controlled load.

> **Security research use only.** You must have explicit written authorization
> from both the vehicle owner and the charging infrastructure operator before
> running HotWire against real hardware. See [SAFETY.md](SAFETY.md).

---

## Features

- Complete DIN 70121 state machine on **both** sides — 12 Res stages (EVSE)
  and 11 Req stages (PEV), from SupportedAppProtocol through SessionStop.
- Two-process loopback validated end-to-end: full handshake reaches
  CurrentDemand in ~5 seconds on `::1`.
- PyQt6 GUI with:
  - Per-stage **Pause** (FSM blocks, modal dialog pops up, user edits params,
    clicks Send)
  - Per-stage **Override** (set params once; FSM applies them every cycle
    without blocking)
  - Live status panel, stage navigator, Req/Res message tree, colored
    trace log
- Attack playbooks — `scripts/attacks/*.py` — that wire up the right
  `PauseController.set_override(...)` calls for you, one command per attack.
- Session recorder — every decoded Req/Res written to JSONL for post-hoc
  analysis.

---

## Quickstart — two-process simulation

No hardware required. Two terminals:

```powershell
# Terminal 1 — EVSE
cd C:\Users\sickcell\hotwire\HotWire
pip install -r requirements.txt
python scripts/run_gui.py --mode evse --sim

# Terminal 2 — PEV
cd C:\Users\sickcell\hotwire\HotWire
python scripts/run_gui.py --mode pev --sim
```

Both GUIs will launch. Click **Start** on each; within ~5 seconds the PEV's
state will reach `WaitForCurrentDemandRes` and the EVSE's StatusPanel will
show the PEV's EVCCID.

The CLI entry points (`scripts/run_evse.py`, `scripts/run_pev.py`) are the
headless equivalents and are what `tests/test_two_process_loopback.py` uses
under the hood.

---

## Attack playbooks

```powershell
# A1 — Autocharge impersonation: spoof EVCCID on the PEV side.
python scripts/attacks/autocharge_impersonation.py --evccid DEADBEEF1234

# A2 — Sustained forced discharge: lie about EVSEPresentVoltage AND
#      EVSEPresentCurrent throughout both PreCharge and the CurrentDemand
#      loop so the PEV closes its contactors and *keeps* them closed.
python scripts/attacks/forced_discharge.py --voltage 380 --current 10
```

Each playbook prints the overrides it applies, spawns the appropriate GUI,
and exits when you press Ctrl-C or close the window. Full docs in
[docs/attacks.md](docs/attacks.md).

## Session analysis

Every GUI run records a JSONL session log under `sessions/<mode>_<ts>.jsonl`.
Post-processing scripts:

```powershell
# Anonymise before sharing (replaces EVCCID / SessionID / IP with stable tags).
python scripts/redact_session.py sessions/*.jsonl --out sessions/anon/

# Compare a clean session against an attack session, field by field.
python scripts/compare_sessions.py baseline.jsonl attack.jsonl --only-differences

# Export to pcap for Wireshark + dsV2Gshark.
python scripts/export_pcap.py sessions/EVSE_20260418.jsonl --out evse.pcap
```

## Protocol selection

EVSE-side `hotwire.ini` has `protocol_preference = prefer_din | prefer_iso |
din_only | iso15118_2_only`. When a PEV offers multiple schemas, the EVSE
picks per this config. PEV side accepts `--protocol din | iso | both | tesla`
via a forthcoming `run_pev.py --protocol` flag.

---

## Repository layout

```
hotwire/
    core/                 addressManager, connMgr, worker, hardware_interface
    fsm/                  fsm_evse, fsm_pev, PauseController, MessageObserver
    exi/                  OpenV2Gx connector + bundled prebuilt codec
    plc/                  tcp_socket, simulation (pure-software SLAC/SDP)
    gui/                  PyQt6 main window, widgets, worker thread
    attacks/              Attack base class + playbook runner
scripts/
    run_evse.py           headless EVSE
    run_pev.py            headless PEV
    run_gui.py            GUI (both modes, mode-dialog at startup)
    attacks/              one-shot attack scripts
tests/                    27 pytest tests + 4 dual-GUI scenarios
vendor/
    OpenV2Gx/             upstream EXI codec source (git submodule, LGPL-3.0)
    build_openv2g.py      reproducible rebuild from vendor source
archive/legacy-evse/      original pyPLC source we ported from (GPL-3.0)
docs/
    attacks.md            attack playbook reference
    paper_compliance.md   how the code maps to the paper claims
```

---

## How MAC / EVCCID spoofing works in HotWire

The paper describes *"MAC address spoofing at the firmware level through
direct modem register manipulation"*. HotWire implements the **equivalent at
the protocol layer**: the PEV FSM's outbound `SessionSetupReq` carries an
`EVCCID` field sourced from the local MAC, and the `PauseController` lets
the GUI or an attack playbook replace it with an arbitrary 12-char hex
string before EXI encoding. On the real charging station this is
observationally identical to a hardware MAC swap — the station's backend
only sees the value inside `SessionSetupReq`.

For a true firmware-level MAC rewrite on a QCA7005 PLC modem you also need
the vendor's PIB tools; we do not ship those because they require hardware
we cannot bundle.

See [docs/paper_compliance.md](docs/paper_compliance.md) for the full
feature-by-feature mapping.

---

## Tests

All run against the bundled OpenV2G codec and the pure-software simulation
layer (no hardware required):

```powershell
pip install -r requirements.txt
python -m pytest tests/ -v                          # 27 unit / smoke tests
python tests/test_two_process_loopback.py           # end-to-end headless
python tests/test_gui_dual_scenarios.py             # 4 dual-GUI attack scenarios
```

Everything should pass. If `pytest-qt` fails to import, you're missing a Qt
install — `pip install PyQt6 pytest-qt` fixes it.

---

## Building OpenV2G from source

The shipped `hotwire/exi/codec/OpenV2G.exe` comes from a private pyPLC-author
branch that includes a "custom parameters" extension (needed for Attack 1's
EVSEID override). The vendored [`OpenV2Gx`](vendor/OpenV2Gx) submodule is
upstream `master`, which does **not** have that patch.

```powershell
# Default — auto-detects MSYS2 UCRT64 / Vagrant's MinGW / anaconda's gcc.
python vendor/build_openv2g.py

# Override compiler:
python vendor/build_openv2g.py --cc /c/msys64/ucrt64/bin/gcc.exe

# Preview without executing:
python vendor/build_openv2g.py --dry-run
```

**Warning:** rebuilding from upstream `master` will break Attack 1's EVSEID
override (the custom-params feature is missing). See
[ATTRIBUTION.md](ATTRIBUTION.md) for the full explanation and the pending
upstream-patch work.

---

## License

HotWire is released under **GPL-3.0-or-later**, matching its upstream
dependencies:

- pyPLC (uhi22) — GPL-3.0-or-later
- OpenV2G / OpenV2Gx (Siemens AG) — LGPL-3.0-or-later
- PyQt6 (Riverbank) — GPL-3.0 or commercial

Full text in [LICENSE](LICENSE). Third-party attributions in
[ATTRIBUTION.md](ATTRIBUTION.md).

---

## Related work

HotWire's DIN 70121 FSMs, connection manager, and address manager are
adapted from [uhi22/pyPLC](https://github.com/uhi22/pyPLC). The EXI codec
comes from [uhi22/OpenV2Gx](https://github.com/uhi22/OpenV2Gx), itself a
fork of [OpenV2G](https://sourceforge.net/projects/openv2g/) by Siemens AG.
