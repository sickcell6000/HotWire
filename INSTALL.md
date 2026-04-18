# Installing HotWire

HotWire is a Python 3.9+ project. The shipped prebuilt `OpenV2G.exe` codec
is Windows x64. Linux / macOS paths are described at the end.

## Quick path — Windows

1. **Python 3.9 or later.**

   ```powershell
   python --version
   ```

2. **Clone with submodules.** The OpenV2Gx source lives in
   `vendor/OpenV2Gx/` as a git submodule.

   ```powershell
   git clone --recurse-submodules <repo-url> HotWire
   cd HotWire
   ```

   If you already cloned without `--recurse-submodules`, run:

   ```powershell
   git submodule update --init --recursive
   ```

3. **Install Python dependencies.**

   ```powershell
   pip install -r requirements.txt
   ```

   This installs `PyQt6`, `pytest`, `pytest-qt`, `pypcap`, `requests`, and the
   usual dev tooling. `PyQt6-Qt6` (the 78 MB Qt runtime) is pulled in as a
   transitive dependency automatically.

4. **Run the tests** to confirm the toolchain works end-to-end:

   ```powershell
   python -m pytest tests/ -v
   # Should show "27 passed" plus a couple of PytestReturnNotNoneWarning
   # (harmless — the pre-Checkpoint-3 tests use `return` instead of `assert`).
   ```

5. **Try the dual-GUI flow.**

   ```powershell
   # Terminal 1
   python scripts/run_gui.py --mode evse --sim
   # Terminal 2
   python scripts/run_gui.py --mode pev --sim
   ```

   Click **Start** in each. Within 5 seconds the PEV advances to
   `WaitForCurrentDemandRes` and the EVSE shows its EVCCID.

## Linux / macOS

Everything Python-side works unchanged. The bundled `OpenV2G.exe` does
not — you need to rebuild from the submodule source:

```bash
# Install a C compiler (pick one):
# Ubuntu/Debian
sudo apt install build-essential
# macOS
xcode-select --install

# Build. The script auto-detects `gcc` / `clang` on PATH.
python vendor/build_openv2g.py
```

The resulting `OpenV2G` binary is installed into
`hotwire/exi/codec/OpenV2G`. `hotwire/exi/connector.py` already picks the
right name based on `os.name`.

**Important caveat:** the upstream OpenV2Gx `master` branch does not
include the "custom parameters" patch that Attack 1 (EVCCID override)
depends on. The rebuilt binary will still handle the full DIN 70121
handshake, but `EDa_1_<EVSEID_hex>` arguments will be silently
discarded. See [ATTRIBUTION.md](ATTRIBUTION.md) for details.

## Real PLC hardware

Running against a real charging station requires:

- A Raspberry Pi 4 (or other Linux SBC) as the host
- A **QCA7005**-based HomePlug Green PHY modem (e.g. modified TP-Link
  adapter) connected over SPI
- A CCS-1 or CCS-2 physical connector with PLC coupling transformers
- Appropriate high-voltage isolation — see [SAFETY.md](SAFETY.md)

The hardware integration path **is not yet implemented** in this repo.
All FSMs and the GUI work; the `pypcap`-based SLAC/SDP driver from the
original pyPLC project has not been re-ported. Use `--sim` for now.

## Configuration

`config/hotwire.ini` is the template. For a local run that doesn't bleed
into git, copy it to the project root — the gitignore exempts that:

```bash
cp config/hotwire.ini hotwire.ini
# Edit hotwire.ini for your environment, e.g. set a different TCP port.
```

Or point `HOTWIRE_CONFIG=/path/to/your.ini` in the environment before
launching any HotWire script.

## Troubleshooting

**`ImportError: DLL load failed while importing QtCore`** — PyQt6 wheel
mismatched your Python version. Force a reinstall:

```powershell
pip install --force-reinstall --upgrade PyQt6 PyQt6-Qt6 PyQt6-sip
```

**`Address already in use` on `::1:57122`** — a previous HotWire instance
didn't release the port. Wait ~30 seconds for the TIME_WAIT to expire, or
override the port in `hotwire.ini` (`tcp_port_alternative`).

**`OpenV2G.exe` crashes silently on Linux** — you rebuilt with a compiler
that doesn't match your glibc. Try running `vendor/build_openv2g.py
--dry-run` to see which compiler was picked, then point at a system one
with `HOTWIRE_CC=/usr/bin/gcc python vendor/build_openv2g.py`.

**GUI hangs on Start** — the worker thread raised an uncaught exception.
Check the trace log panel (right side of the window); it will show the
traceback. Common causes: no network interface with IPv6 link-local,
missing `hotwire.ini` at the default search paths, or an `OpenV2G.exe`
binary from an incompatible branch.
