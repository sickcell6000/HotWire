# HotWire Hardware Design Guide

> Full reference for HomePlug AV / CCS bench hardware, compiled from pyPLC upstream docs, OpenInverter forum, open-plc-utils issue tracker, and TP-Link community recovery threads. Written to be operator-runnable without further research.

---

## 0. TL;DR — "my modem stopped working"

If your modem responds to `int6k -I` but payload is all zeros, and `plcstat -t` is empty:

1. **Run `plctool -r <MAC>` not `int6k -r`.** If `MVERSION` = `"BootLoader"` → modem is stuck in bootloader (no runtime firmware running).
2. **Unplug modem from mains for 30 s, plug back in.** Clears transient brown-out wedges.
3. If still stuck → **RAM-boot it**: `plctool -i <iface> -P <model>.pib -N <fw>.nvm 00:B0:52:00:00:01`. Works for this session only.
4. If Level 3 RAM-boot works → flash permanently: `plctool -S <softloader>.nvm -P <pib> -N <fw> -FF <MAC>`. Softloader file is rare — see §7.
5. Hardware replacement is often the rational answer for a $15 consumer adapter — see §7.6.

---

## 1. Chipset decision tree

| Chipset | Toolchain | SLAC in factory FW? | Bench suitability |
|---|---|---|---|
| **Qualcomm AR7420 / QCA7420** | `plctool` / `plcstat` (open-plc-utils) | Disabled in later FW; re-enable via PIB patch | ★★★★★ Canonical, cheap, documented |
| Qualcomm QCA7005 | SPI-host protocols (no Ethernet interface) | Native | ★★★★ For embedded (ccs32clara, foccci) |
| Qualcomm QCA7500 | `plctool` | **Does NOT send SLAC param requests** even after patching | ✗ Unusable for PEV |
| Intellon INT6400A1G | `int6k` | SLAC visible on air, `CM_SET_KEY` fails | ★★ Sniff-only |
| Unicomsemi MSE1060 (TP-Link V6) | **None** | N/A | ✗ Avoid — no toolchain |
| Lumissil CG5317 | Lumissil SDK (not open) | Native ISO15118 + DIN70121 + J3400 | Future — automotive Tier-1 path |

**Use the right tool per chipset**: running `int6k` against a QCA7420 can return "successful" responses with zero payload — misleading symptom. Always use `plctool` on QCA7420.

### Recommended cheap hardware

- **TP-Link TL-PA4010 / TL-PA4010P V2.0 or V5.0** — QCA7420. V2.3 has a FW bug that drops connections randomly. V5.0 is the sweet spot.
- **Do NOT buy V6.0** — Unicomsemi chipset, zero tooling support.
- Pass-through variant (PA4010**P**) has more case room for the DC + coupling mods.

---

## 2. TP-Link PA4010 physical modification

### Step-by-step (per pyPLC `doc/hardware.md`)

1. Pry the case open along the clamshell seam.
2. **Desolder the AC prongs and the on-board AC→DC rectifier/primary section.** Keep the 3.3 V buck — it still generates Vcore from whatever you now feed onto the original 12 V rail.
3. **Inject DC into the original 12 V trace** (NOT the 3.3 V trace — that would bypass the regulator).
4. Replace the on-board line-coupling network with:
   - **1 nF capacitor** in series with **150 Ω resistor**, between the secondary winding of the internal RF transformer and your external CP line.
   - Polarity irrelevant (it's AC-coupled RF 2–30 MHz).
   - Tolerance: 33–220 Ω and 0.5–2.2 nF all work. Higher caps slow the 1 kHz PWM edges.

### Six acceptable coupling topologies

Any order of `1nF — 150Ω — transformer_secondary` between CP and PE works. pyPLC `TPlink_RF_transformer_and_coupling_network.jpg` shows the canonical, `RF_coupling_six_variants.jpg` shows all six equivalents.

### Why coupling matters

Without the series C+R, the 12 V 1 kHz PWM pilot signal leaks into the RF stage and either destroys the modem analog front end or corrupts the SLAC signal level measurement. The 1 nF is sized to block the PWM (1 kHz → high Z) while passing HomePlug AV RF (2–30 MHz → low Z). The 150 Ω sets TX output impedance matching.

---

## 3. DC power supply — ranges and pitfalls

### Confirmed working (pyPLC hardware.md, TL-PA4010P V5.0)

| Vin | I | Transmit power |
|---|---|---|
| 13 V | 110 mA | Full |
| **12 V** | **120 mA** | **Full (original design point)** |
| 10 V | 120 mA | Full |
| 6 V | 190 mA | Reduced |
| 5 V | 220 mA | Reduced (USB power-bank OK) |
| 4 V | 240 mA | Marginal bench-only |

### Why reduced TX is *fine* for CCS bench

PA4010 is designed to punch through home mains wiring over tens of metres. On a 1 m CP jumper between PEV and EVSE emulator, TX is ~30 dB stronger than necessary. Reduced TX actually **reduces cross-talk** between nearby test rigs.

### Known brown-out wedge pattern

Forum reports link:
- Unstable DC rail (especially shared with a relay that clicks at start-up)
- 12 V sagging below ~3.5 V during the modem's firmware-load window
- Capacitor ESR rise on 3.3 V Vcore rail
- PLC TX bursts pulling sharp current dips

...to the modem getting stuck in **bootloader-only mode** (§6). The firmware starts to load from SPI NOR, the Vcore droops during the read, bootloader aborts load, stays in bootloader.

### Recommendations

- **Dedicated 5–12 V bench supply** or USB power bank per modem. Not shared with relay coil.
- If you must DC-inject, **connect to the original 12 V trace** of the PA4010 board, not the 3.3 V rail.
- If using a USB-C PD adapter: confirm the output is clean (some cheap PD supplies have 200+ mV ripple under load transient).

---

## 4. PIB programming — the six-command universal pattern

**Every CCS modem provisioning workflow in the entire open-source ecosystem** uses these six commands. Memorise:

```bash
# 1. Read current PIB for safekeeping
plctool -i eth0 -p original_<MAC>.pib <MAC>

# 2-5. Patch offsets in a copy
cp original_<MAC>.pib pev_<MAC>.pib
setpib pev_<MAC>.pib 74   hfid  "PEV"      # human-friendly ID string
setpib pev_<MAC>.pib F4   byte  1          # role byte — 1 = PEV, 2 = EVSE
setpib pev_<MAC>.pib 1653 byte  1          # again, same role, different offset
setpib pev_<MAC>.pib 1C98 long  10240 long 102400   # AVLN timing params

# 6. Write the patched PIB back into flash
plctool -i eth0 -P pev_<MAC>.pib <MAC>
```

For the EVSE side, substitute `"EVSE"` at offset 0x74 and `2` at 0xF4 / 0x1653.

### Critical detail: `-R <MAC>` targeting

Without `-R <MAC>` argument to `plctool -P`, some plctool builds write to **every** modem on the wire — can corrupt a working partner. Always specify the target MAC explicitly.

### Why the PIB patch is necessary at all

QCA deliberately removed SLAC from later factory firmware builds. The `docbook/ch01s10.html` in open-plc-utils lists `slac` as deprecated. The PIB patch at offsets 0xF4/0x1653 enables "forward management frames (0x88E1) to Ethernet", so the host (pyPLC / HotWire) can see and drive the SLAC handshake itself.

### What gets stored where

- **NMK / NID** are NOT in the PIB you flash — they're negotiated at runtime by SLAC + `CM_SET_KEY`. Don't confuse the default `50D3E4933F855B7040784DF815AA8DB7` DAK/NMK constants in plctool source with something you have to set.
- **Role byte** (PEV vs EVSE) **is** in PIB and permanent across power cycles.
- **HFID string** is in PIB, cosmetic only (shows up in `plcstat -t`).

### Files to checkin or keep offline

**None of the open-source CCS repos commit PIB files** — they're per-modem (hardware revision + region + original MAC all matter). Keep your `original_<MAC>.pib` snapshots offline; they're the only way to restore a modem to shipped state if you overwrite wrong.

---

## 5. Coupling network & analog front-end BOM

### Minimum mini-EVSE bench setup (from pyPLC `hardware/plc_evse/`)

| Part | Value | Purpose |
|---|---|---|
| PA4010 modified | — | HomePlug AV TX/RX |
| Coupling cap | 1 nF | Block PWM, pass RF |
| Coupling resistor | 150 Ω | TX impedance match |
| PP resistor | 1.5 kΩ | Proximity pilot sense (inlet side) |
| CP pull-up | 1 kΩ | To +12 V |
| 1 kHz 5% PWM generator | Arduino Nano | Via WallboxArduino sketch (D10) |
| Contactor relay | SPDT 12 V coil | Driven from D8 |
| LED strip | WS2812B on D11 | Status indicator |
| CP feedback divider | 56k + 100k + 220k | A1 ADC read of CP level |

KiCad schematic: https://github.com/uhi22/pyPLC/blob/master/hardware/plc_evse/plc_evse_schematic_v1.pdf

### Dieter HV / LV boards (for high-voltage inlet measurement)

Needed only when connecting to a **real** CCS charger or testing DC current flow. Bench protocol-only (HotWire) doesn't need Dieter.

**DieterHV** (ADC front-end):
- Arduino Pro Mini 5 V
- Divider from CCS inlet DC (usually 400 V max) down to 0–5 V range
- Isolated 5 V → 5 V DC-DC (B0505S-1W)
- PC900V optocoupler on serial TX
- Outputs 19200 baud `inlet_v=<int>` lines

**DieterLV** (CP control + relay drive):
- Arduino Pro Mini 5 V
- Switches a 1.2 kΩ resistor in parallel with permanent 2.7 kΩ to transition PEV CP state B ↔ C
- Drives two relays via simple BJT drivers
- Receives serial `cp=1`, `contactor=1`, etc.

Both sketches: https://github.com/uhi22/dieter

Communication: 19200 baud serial. HotWire's `RealHardwareInterface` (Checkpoint 19) speaks this "celeron55" dialect natively.

---

## 6. Modem wedge diagnosis matrix

### Definitive test: VS_SW_VER MVERSION field

```bash
plctool -i eth0 -r <MAC>
```

| MVERSION output | State | Recovery |
|---|---|---|
| `MAC-QCA7420-1.5.0.0026-02-CS-20200114` (or similar) | Runtime firmware loaded, healthy | Nothing to do |
| `"BootLoader"` | Stuck in bootloader only | Level 2+ recovery (§7) |
| Empty / zeros / `"?"` | MME handler missing (wrong tool?) | Try `plctool` vs `int6k` mismatch first |
| Timeout / no response | Modem power-off, Ethernet broken, or brick | Physical check |

### Related symptoms that map to "bootloader mode"

All of these together = modem is in bootloader, not runtime:

- `plctool -I` returns a frame, but MAC / HFID / FW fields are all zero
- `plcstat -t` header row only, no device lines
- `int6k -I` "works" but payload zeros (int6k is wrong tool for QCA7420)
- Peer modem on same PLC line never sees this modem (no AVLN participation)
- `00:B0:52:00:00:01` MAC responds to commands (bootloader default address)

### Why VS_RD_MOD returns zeros in bootloader

Per `docbook/firmware.xml` in open-plc-utils:

> *"The AR7420 bootloader recognizes only VS_SW_VER, VS_RS_DEV, VS_WRITE_AND_EXECUTE and VS_RAND_MAC_ADDRESS requests."*

`VS_RD_MOD` is runtime-only. Bootloader ACKs the frame at MAC layer but the handler returns zero-filled payload because it doesn't implement reads of the SDRAM regions being requested.

### The TP-Link Utility brick pattern

TP-Link's own `tpPLC` Windows utility, when updating V3 hardware, has a well-documented failure mode: it erases the **softloader** module from flash and then fails to write the new firmware. Subsequent `plctool -F` returns:

```
No NVM Softloader Present in Flash Memory (0x71): Device refused request
```

The softloader is the small stub that the bootloader chain-loads to then chain-load the main firmware. Without it, `plctool -F` has no path to write flash. **Never run the stock TP-Link Windows utility against a modem you want to use for CCS.**

---

## 7. Recovery procedures — ranked by invasiveness

### Level 0 — Diagnose only (no changes)

```bash
plctool -i eth0 -r 00:B0:52:00:00:01    # VS_SW_VER, check MVERSION
plctool -i eth0 -I 00:B0:52:00:00:01    # identity
plcstat -t -i eth0                       # topology
```

Use the bootloader default MAC `00:B0:52:00:00:01` — if modem is in bootloader, the real MAC is not responsive until runtime FW loads.

### Level 1 — Mains-cycle the modem

**Not** a host reboot. Unplug the modem from its power source for **30 seconds** (not 2 seconds — bulk caps need time to discharge fully). Plug back in.

Clears:
- Transient brown-out that caused bootloader re-entry
- Firmware watchdog lock-ups
- Overheated Vcore rails

Community consensus: if Level 1 doesn't fix it, you have a persistent problem (flash corruption, softloader wipe, hardware failure).

### Level 2 — RAM boot (non-persistent, confirms silicon healthy)

```bash
plctool -i eth0 \
    -P <model>.pib \
    -N FW-QCA7420-1.5.0.0026-02-CS-20200114.nvm \
    00:B0:52:00:00:01
```

Loads firmware + PIB **into SDRAM only**. Works until next power cycle. If this works:
- Silicon is fine
- Flash contents are bad but flash chip itself is OK
- Proceed to Level 3 to make it persistent

If this FAILS: move to Level 5 (UART/SPI).

### Level 3 — In-band reflash with softloader

```bash
plctool -i eth0 \
    -S AR7420-softloader.nvm \
    -P <model>.pib \
    -N <firmware>.nvm \
    -FF \
    <MAC>
```

Writes softloader + PIB + firmware to flash permanently. **`-FF`** = force-flash (ignore checksum warnings).

**Blocker**: Qualcomm never publicly released the AR7420/QCA7420 softloader `.nvm`. Community sources pass it around privately. Some users extract it via `plctool -r` chain ops from a still-working sibling modem; results unreliable.

If you don't have a softloader `.nvm`, you can only run the modem by re-doing Level 2 every power-up. For a bench rig this may actually be acceptable — include it in your test setup script.

### Level 4 — tpPLC Windows utility after RAM boot

Documented success on TP-Link forum topic 219718 / Fitzcarraldo blog:

1. Do Level 2 (RAM boot the modem)
2. Immediately run the **V3-compatible** tpPLC GUI utility for Windows
3. The now-running runtime firmware exposes a flash-write path the bootloader alone doesn't

This relies on tpPLC to write via the runtime flash API rather than the bootloader API. Get the right version of tpPLC — mismatched versions re-brick rather than unbrick.

### Level 5 — UART / SPI flash programmer (chip-off)

Open the modem case. Two hardware paths:

**UART bootloader interrupt**:
- Locate 4-pin header near QCA7420 (TX/RX/GND/VCC, 3.3 V TTL — NOT RS-232 levels)
- Connect FTDI adapter, 115200 8N1
- Tap keys during early boot to drop into bootloader prompt
- Commands vary by QCA7420 firmware generation — experimentation required
- Reference: https://redacacia.me/2013/03/07/debrick-your-tl-mr3420-router-with-a-serial-ttl-cable/ (TP-Link router, similar flow)

**SPI flash chip-off**:
- QCA7420 uses external 25-series SPI NOR flash, typically SOP-8, 1–2 MB
- Clip-on with Pomona 5250 or desolder
- Program with CH341A ($3) or FlashcatUSB ($80+)
- Image must contain: softloader + PIB + firmware, in QCA7420 flash layout
- Wrongbaud's methodology: https://wrongbaud.github.io/posts/router-teardown/

**As of 2026, no public teardown of TL-PA4010 UART/SPI pinouts exists.** You're doing original reverse engineering.

### Level 6 — JTAG

Pads exist near the QCA7420 ARM core (TMS/TDI/TDO/TCK/TRST). Not broken out on any published TL-PA4010 PCB. Requires J-Link or FT2232 with custom QCA7420 config. Last-resort; if Level 5 UART fails, Level 6 is unlikely to succeed without insider documentation.

### Level 7 — Replace the modem

**$15 for a new PA4010 V5 vs. hours of chip-off rework.** For bench work, keeping 2–3 spare modems ready is cheaper than fixing a wedged one. Consumer-grade silicon, not worth resurrecting.

---

## 8. Alternatives to TP-Link PA4010

### If you're starting fresh

| Option | Price | Pros | Cons |
|---|---|---|---|
| **TP-Link PA4010 V5** | $15 | Documented, community-supported | Wedge risk, needs HW mod |
| **Codico RED beet 2.0** | $80 | QCA7006AQ, SPI for embedded MCU | SPI host needed |
| **Codico WHITE beet PI/EI** | $200+ | Full ISO15118 stack onboard, includes PnC | Price |
| **foccci board (uhi22)** | DIY | Open-hardware, STM32 + QCA7005 | Assembly required |
| **Lumissil IS32CG5317 eval** | TBD | Current automotive silicon (BMW, Amperfied) | Lumissil SDK, not open |

### If you need to sniff real cars charging at real stations

Devolo dLAN 200 AVplus (Intellon INT6400A1G). Only cheap consumer adapter that **passively** captures SLAC between a real car and a real charger. Cannot act as endpoint (`CM_SET_KEY` fails), but for protocol debugging / pcap capture in the wild this is the reference.

---

## 9. Linux host setup gotchas

### Disable NetworkManager on eth0

NetworkManager does DHCP + RouterSolicitation on any "up" Ethernet port by default. The DHCP packets disturb SLAC measurements.

```bash
# Option 1: NetworkManager config
nmcli con add connection.interface-name eth0 type ethernet connection.id CCS
# Edit /etc/NetworkManager/system-connections/CCS:
#   [ipv4] method=disabled
#   [ipv6] addr-gen-mode=stable-privacy
#          method=link-local
nmcli con up CCS

# Option 2: mask entirely
sudo systemctl stop NetworkManager
sudo ip link set eth0 up
# then manually assign link-local:
sudo ip -6 addr add fe80::1/64 dev eth0
```

### pcap without sudo

```bash
which python3     # e.g. /usr/bin/python3
sudo setcap cap_net_raw,cap_net_admin=eip /usr/bin/python3.11
```

### Install open-plc-utils

Debian/Ubuntu:
```bash
sudo apt install plc-utils plc-utils-extra
```
(May be named differently per distro; from source: `git clone https://github.com/qca/open-plc-utils && cd open-plc-utils && make && sudo make install`).

### Wireshark / dumpcap permission on headless Pi

dumpcap wants to drop privileges to `wireshark` group. When launched from `sudo python`, it loses caps on exec. Workarounds:

```bash
sudo usermod -a -G wireshark pi
sudo setcap cap_net_raw,cap_net_admin=eip /usr/bin/dumpcap
# OR run dumpcap with -Z root (no drop):
sudo dumpcap -i eth0 -q -w capture.pcap -f 'ether proto 0x88E1' -Z root
```

---

## 10. Windows host setup gotchas

### Interface name format

Pcap on Windows uses NPF device paths, not friendly names:

```
\Device\NPF_{4C95DA23-E78D-4555-8861-C0F158E9F74E}
```

Resolve via:
```powershell
Get-NetAdapter | Where-Object { $_.InterfaceDescription -like '*PLC*' }
```

### pcap-ct vs pypcap

**Install pcap-ct (pure Python ctypes wrapper), NOT pypcap.** pypcap needs a C build against Npcap SDK; pcap-ct is a pip install:

```powershell
pip install pcap-ct
```

Both expose `import pcap`.

### Npcap install

Download from https://npcap.com/. During install check:
- ☑ WinPcap API-compatible Mode
- ☐ Restrict to administrators (unchecked = any user can pcap)

### Socket scope-id in Windows

IPv6 link-local addresses carry a scope id via the 4-tuple `(host, port, flowinfo, scope_id)`, but Windows **rejects** `bind(("::", port, 0, scope_id))` with `WinError 10049`. Bind to unscoped `::` and rely on `IPV6_JOIN_GROUP(mreq_with_scope)` for multicast subscription. (HotWire Checkpoint 19 fixes this in SdpServer.)

---

## 11. Known-good firmware versions

```
Runtime firmware:  FW-QCA7420-1.5.0.0026-02-CS-20200114.nvm
Older fallback:    MAC-QCA7420-1.3.0.2134-00-20151212-CS
                   MAC-QCA7005-1.1.0.730-04-20140815-CS  (Ioniq OEM)
```

Download via open-plc-utils `firmware/` directory or extract from a working sibling modem:

```bash
plctool -i eth0 -n backup_<MAC>.nvm <MAC>      # save NVM from running modem
plctool -i eth0 -p backup_<MAC>.pib <MAC>      # save PIB
chknvm -v backup_<MAC>.nvm                     # validate before using elsewhere
chkpib -v backup_<MAC>.pib
```

Store backups per-MAC: `firmware/backups/34_E8_94_07_A4_FB.nvm` etc.

---

## 12. Troubleshooting matrix (most common → rare)

| Symptom | First check | Likely cause | Fix |
|---|---|---|---|
| `plcstat -t` empty + `int6k -I` zero payload | Tool mismatch — `int6k` vs `plctool` | Using `int6k` on QCA7420 | Switch to `plctool` |
| `plcstat -t` empty + `plctool -r` returns `"BootLoader"` | Runtime FW not loaded | Flash corrupt or softloader wiped | Level 1 mains cycle; then Level 2 RAM boot; then Level 3 if softloader available |
| SLAC_PARAM.REQ sent but EVSE sees 0 frames | Two modems not in same AVLN | Fresh SLAC session changed NMK; peer not pre-paired | Config `plc_nmk_hex` + `plc_nid_hex` so both sides program same AVLN (HotWire Checkpoint 20) |
| Modem "randomly drops connection" | HW revision | V2.3 FW bug | Upgrade to V5.0, or flash newer NVM |
| `plctool -F` → "No NVM Softloader Present" | Softloader wiped | TP-Link utility corruption | Source softloader; or Level 5 SPI reflash |
| PWM pilot signal destroyed | Coupling cap too large | >2.2 nF slows edges | Replace with 1 nF |
| Modem answers first hour, then dies | Thermal / brown-out | Capacitor ESR rise or inadequate heatsinking | New modem |
| `CM_SET_KEY.REQ` sent but no CNF | Local modem not responding | Modem in bootloader mode (zero fw) | Recovery §7 |
| Pi eth0 RX=0 but TX works | Physical layer | **Cable not fully seated** — not software! | Reseat the RJ45 |
| `phase4_v2g` FAIL after `phase4_v2g` PASS | AVLN drift between sessions | Random NMK each SLAC run | Config stable NMK, or pre-pair step (HotWire Checkpoint 20) |

---

## 13. Testing checklist before declaring a bench "ready"

### Minimum pre-flight

```bash
# Both hosts
plctool -r <MAC>                     # MVERSION contains version string, not "BootLoader"
plcstat -t -i eth0                    # LOC row present
int6k -I <MAC> | hexdump -C          # non-zero firmware version bytes

# On the wire (either side)
sudo tcpdump -c 5 -i eth0 'ether proto 0x88E1'   # see peer modem heartbeat within 30 s
```

### Full HotWire phase sequence

```bash
# phase0 — env + hw preflight
sudo python3 scripts/hw_check/phase0_env.py -i eth0
sudo python3 scripts/hw_check/phase0_hw.py  -i eth0

# phase1 — HPAV sniff
sudo python3 scripts/hw_check/phase1_link.py -i eth0 --duration 10 --min-frames 1

# phase2 — SLAC (the most reliable signal of 'modems OK')
sudo python3 scripts/hw_check/phase2_slac.py -i eth0 --role pev  --mac <PEV-MAC>  --budget 30
sudo python3 scripts/hw_check/phase2_slac.py -i eth0 --role evse --mac <EVSE-MAC> --budget 30

# phase3 — SDP
sudo python3 scripts/hw_check/phase3_sdp.py -i eth0 --role pev  --budget 20
sudo python3 scripts/hw_check/phase3_sdp.py -i eth0 --role evse --budget 30

# phase4 — V2G full session
sudo python3 scripts/hw_check/phase4_v2g.py -i eth0 --role pev  --budget 60 --min-cd 3
sudo python3 scripts/hw_check/phase4_v2g.py -i eth0 --role evse --budget 90
```

Expected: phases 1-4 all PASS, `plcstat -t` shows LOC + REM in same AVLN with the NID from `config/hotwire.ini`.

---

## 14. References

### Primary sources

| URL | Content |
|---|---|
| https://github.com/uhi22/pyPLC | Python reference; `doc/hardware.md` + `doc/EvseMode.md` |
| https://github.com/uhi22/pyPLC/blob/master/hardware/plc_evse/plc_evse_schematic_v1.pdf | Mini-EVSE schematic |
| https://github.com/qca/open-plc-utils | Canonical QCA toolchain |
| https://github.com/qca/open-plc-utils/blob/master/docbook/firmware.xml | Bootloader MME support |
| https://github.com/qca/open-plc-utils/blob/master/docbook/firmware-7420-flash.xml | QCA7420 flash layout |
| https://github.com/qca/open-plc-utils/issues/135 | Softloader wipe recovery |
| https://github.com/uhi22/foccci | Open-hardware EVCC reference |
| https://github.com/uhi22/dieter | Arduino HV/LV sketches |
| https://github.com/uhi22/ccs32clara | STM32 embedded PEV reference |
| https://openinverter.org/forum/viewtopic.php?t=3551 | Main "Drawing power out of CCS" thread |
| https://openinverter.org/wiki/CCS_EVCC_using_AR7420 | Wiki article |
| https://community.tp-link.com/us/home/forum/topic/219718 | Softloader recovery discussion |
| https://fitzcarraldoblog.wordpress.com/2020/07/22/updating-the-powerline-adapters-in-my-home-network/ | Step-by-step reflash |
| https://manpages.debian.org/testing/plc-utils/plctool.1 | plctool manpage |

### Secondary

| URL | Content |
|---|---|
| https://community.tp-link.com/en/home/forum/topic/83065 | PA211 capacitor failure patterns |
| https://www.snbforums.com/threads/defective-capacitors-in-actiontec-powerline-adapters.38991/ | Powerline adapter brown-out pattern |
| https://redacacia.me/2013/03/07/debrick-your-tl-mr3420-router-with-a-serial-ttl-cable/ | TP-Link UART debrick (router, similar silicon) |
| https://wrongbaud.github.io/posts/router-teardown/ | SPI flash chip-off methodology |
| https://www.codico.com/en/products/powerline-communication/homeplug-green-phy-for-charging-stations | Codico beet modules datasheet index |

### Research caveat

Three OpenInverter forum threads (t=3551, t=2262, p=37085, p=55120) are blocked by Anubis anti-bot and cannot be fetched programmatically. They remain the best source for operator-level war stories. Read them in a real browser when possible.
