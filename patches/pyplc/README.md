# pyPLC Windows runtime patches

Three small patches to upstream pyPLC (https://github.com/uhi22/pyPLC) that
together make `python pyplc.py E` runnable on Windows. Without them a clean
checkout exits within 0.4 s of launching, mid-print buffer, looking like it
"stopped at `listening on port 57122`".

None of the patches change pyPLC's state-machine semantics. They only make
the process survive long enough to run its main loop so the operator can
observe what happens next (and so HotWire can compare its own V2G behaviour
side-by-side with pyPLC's).

---

## What actually happens on Windows without the patches

```
t=0.3s  pyPlcTcpSocket listening on port 57122   <- last line that flushes
t=0.4s  transmitting SET_KEY.REQ ...              <- buffered, never printed
t=0.4s  sniffer.sendpacket(...) raises OSError 31
t=0.4s  main loop tears down, Python exits, console window closes
```

Console output buffering plus the `tkinter.root.update()` message pump hide
the traceback; on the screen you only see `listening on port 57122` and then
the window disappears. Re-run with `python -u` and redirect to a file to see
the full traceback.

---

## The three patches

### 1. `udplog_respect_disabled_flag.patch`

`udplog.log()` only skips transmission when `purpose == ""` **and** the
`udp_syslog_enable` flag is false. Calls with a non-empty `purpose` still
fall through to `self.transmit()` which hits `Npcap sendpacket`. On Windows
that fails with `ERROR_GEN_FAILURE (31)` when the link-layer peer isn't
cooperating (modem in bootloader mode, cable not fully negotiated, etc).

Patch: honour the disabled flag unconditionally. Logging stays off when the
config says so, regardless of `purpose`.

### 2. `transmit_swallow_oserror.patch`

`pyPlcHomeplug.transmit()` calls `self.sniffer.sendpacket(bytes(pkt))` with
no error handling. A single transient `OSError` from Npcap (modem wedged,
cable jiggled, driver napping) crashes the entire main loop.

Patch: wrap in `try/except OSError`, log once, continue. SLAC / HomePlug
already have retry logic at the protocol level; dropping one frame is
recoverable, crashing Python is not.

### 3. `PYTHONIOENCODING=utf-8` (no file patch; environment variable)

Windows code page 950 (traditional Chinese default) can't encode Cyrillic
or other high-byte bytes that show up in Arduino serial garbage or random
binary payloads. `print()` raises `UnicodeEncodeError` and crashes.

Workaround: set `PYTHONIOENCODING=utf-8` before launching pyPLC.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python pyplc.py E
```

Or permanently at the machine level:

```powershell
[Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'User')
```

---

## Applying the patches

Both patches are unified diffs against upstream pyPLC commit `698dcb1`
(v0.9-66). Apply from inside your pyPLC checkout:

```powershell
cd C:\path\to\pyPLC
git apply path\to\hotwire\patches\pyplc\udplog_respect_disabled_flag.patch
git apply path\to\hotwire\patches\pyplc\transmit_swallow_oserror.patch
```

Or edit by hand — each patch touches exactly one location.

### Convenience launcher

`run_pyplc_windows.ps1` wraps `python -u pyplc.py <mode>` with
`PYTHONIOENCODING=utf-8` and a timestamped log in `logs/`. Drop it
next to `pyplc.py` in your pyPLC checkout and run:

```powershell
.\run_pyplc_windows.ps1 E   # EVSE
.\run_pyplc_windows.ps1 P   # PEV
.\run_pyplc_windows.ps1 L   # Listener
```

If the script misbehaves (PowerShell execution policy), either:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run_pyplc_windows.ps1 E
```

or run the three lines inline:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python -u pyplc.py E 2>&1 | Tee-Object -FilePath pyplc_E.log
```

## Relationship to HotWire

HotWire replaces pyPLC entirely; it does not import from it. These patches
exist solely so an operator can run pyPLC side-by-side with HotWire on the
same bench for comparison / validation. If you're only running HotWire,
ignore this directory.

HotWire itself already handles all three of these issues correctly:

* `hotwire/plc/udplog.py` does not exist — HotWire never emits UDP syslog.
* `hotwire/plc/l2_transport.py::PcapL2Transport.send()` wraps
  `sendpacket()` in try/except from day one.
* HotWire entry scripts (`scripts/hw_check/phase*.py`) set
  `sys.stdout.reconfigure(encoding='utf-8')` where possible, and the
  phase runner uses byte-mode subprocess.PIPE for any serial telemetry.
