# HotWire — Third-party attribution

## pyPLC (GPL-3.0)

HotWire's FSM, connection manager, address manager, PLC/TCP transport and
hardware-interface layers are adapted from **pyPLC** by Uwe Hinrichs
(uhi22). See `archive/legacy-evse/` for the unmodified legacy source we
ported from. Every HotWire file that carries a non-trivial port documents
the adaptation in its module docstring.

- Upstream: https://github.com/uhi22/pyPLC
- License: GPL-3.0-or-later

Because pyPLC is GPL-3.0, HotWire distributes under the same license.

## OpenV2Gx / OpenV2G (LGPL-3.0)

EXI encoding and decoding is delegated to the bundled `OpenV2G.exe`
binary at `hotwire/exi/codec/OpenV2G.exe`. The source code is vendored as
a git submodule under `vendor/OpenV2Gx/` pointing at:

- Upstream: https://github.com/uhi22/OpenV2Gx
- Original OpenV2G: https://sourceforge.net/projects/openv2g/
- Copyright: Siemens AG, 2007-2022
- License: LGPL-3.0-or-later

### Important: the shipped binary is NOT built from the vendored source

The prebuilt `hotwire/exi/codec/OpenV2G.exe` (1,640,695 bytes) came from
the pyPLC author's private / experimental branch that includes a
**"custom parameters" extension** — specifically, the ability to pass
positional arguments like `EDa_1_5A5A4445464C54` to override response
fields (e.g. `ResponseCode`, `EVSEID`). HotWire's Checkpoint 3 pause /
override feature depends on this extension.

The public `master` branch of OpenV2Gx at commit `1ecbedd` (November
2022) does **not** include these patches. If you rebuild OpenV2G from
the submodule source using `vendor/build_openv2g.py`, the resulting
binary will:

- Successfully complete the DIN 70121 handshake (Checkpoint 2 tests pass).
- **Fail** the GUI override scenarios (Checkpoint 3 scenario 2
  "EVSE override: EVSEID spoof" and any test that passes positional
  command-line arguments to encode functions).

### Recommended workflow

1. **Use the shipped `OpenV2G.exe` as-is** on Windows. It's the known-good
   binary tied to all 27 pytest + 4 GUI-scenario tests.
2. If you need a Linux/macOS rebuild, run `python vendor/build_openv2g.py`
   and be aware that custom-params commands (any with positional numeric
   arguments after an underscore) will silently fall back to defaults.
3. A proper fix requires finding or replicating the pyPLC author's
   unreleased patches, or writing the custom-params logic ourselves on
   top of upstream OpenV2Gx. This is a known Checkpoint 5+ item.

To rebuild anyway:

```bash
# Needs gcc / MinGW64 / MSYS2 UCRT64 on PATH or autodetected.
python vendor/build_openv2g.py            # auto-detect compiler
python vendor/build_openv2g.py --dry-run  # preview commands
HOTWIRE_CC=/path/to/gcc python vendor/build_openv2g.py
```

The resulting binary is installed into `hotwire/exi/codec/OpenV2G.exe`.
Back up the original first:

```bash
cp hotwire/exi/codec/OpenV2G.exe hotwire/exi/codec/OpenV2G.exe.backup
```

## PyQt6 (GPL-3.0 / commercial)

HotWire Checkpoint 3's GUI uses PyQt6 under GPL-3.0, which is compatible
with HotWire's GPL-3.0 license. See https://www.riverbankcomputing.com/
software/pyqt/.

## Other dependencies

Standard `pip install -r requirements.txt` dependencies (requests, pytest,
pytest-qt, Flask, black, ruff, mypy) ship under their respective licenses
(MIT / BSD / Apache-2.0 / PSF — all permissive).
