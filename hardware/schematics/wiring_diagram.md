# HotWire Hardware Wiring Diagram

This document describes the bench-level interconnect for the two
roles HotWire plays (EVSE-emulator and PEV-emulator), matching the
hardware described in the paper §5 "Experimental Setup".

**Before assembling anything: read [SAFETY.md](../../SAFETY.md).** The
DC pins on a real CCS cable carry up to 1,000 V and enough current to
kill. The CP (control pilot) + PE (protective earth) pair that HotWire
talks on is safe low-voltage once the inlet is de-energized, but the
HV pair must stay disconnected until you know what you're doing.

---

## Block diagram (both roles)

```
                  +---------------------+
                  |  Raspberry Pi 4     |
                  |  Ubuntu 20.04 LTS   |
                  |  Python 3.9+        |
                  +---+-----------+-----+
                      |           |
                      | Ethernet  | USB-Serial (debug)
                      |           |
              +-------+------+    |
              |              |    |
              | QCA7005      |    |
              | HomePlug     |    |   +----------------+
              | AV Green PHY |    +-->| Arduino Uno    |
              | (modified    |        | Sense + Relay  |
              |  TP-Link     |        | Board          |
              |  adapter)    |        +------+---------+
              +--+-----------+               |
                 |                           |
                 | PLC over CP/PE            | CT + Hall sensor reads
                 | (differential pair)       | + optocoupler-isolated
                 |                           |   emergency disconnect
    +------------+-----+                     |
    |                  |                     |
    | CCS Type-1 cable |       +-------------+----------------+
    | (or bench coupler|       |                              |
    |  for 2-Pi bench) |       |   EVSE-role only:            |
    +---+----------+---+       |   1.25 kW resistive load     |
        |          |           |   (5 × 220 V / 250 W bulbs   |
        | DC+      | DC-       |    in parallel, floating)    |
        |          |           +------------------------------+
        v          v
     [vehicle]  [vehicle]    (or load-bank terminals for EVSE role)
```

The dotted-box load bank only exists on the EVSE role — for Attack 2
(§4.2 "Unauthorized Energy Extraction") the rogue EVSE must sink the
current the vehicle delivers, rather than sourcing it.

---

## Component list (exact models)

| # | Component | Model | Role | Paper ref |
|---|-----------|-------|------|-----------|
| 1 | Single-board computer | Raspberry Pi 4 Model B (4 GB) | Host for HotWire + worker process | §5 |
| 2 | OS | Ubuntu 20.04 LTS 64-bit | Verified target | §5 |
| 3 | HomePlug Green PHY modem | Qualcomm **QCA7005** (inside modified TP-Link PLA5206 adapter) | CCS power-line comms | §5 |
| 4 | PLC coupling | 1:1 isolation transformer, 30 MHz bandwidth | Galvanic isolation between modem and CP/PE | §5 |
| 5 | Cable | CCS Type-1 (SAE J1772 + DC pins) 3 m, 50 mm² copper | Vehicle ↔ EVSE connector | §5 |
| 6 | Emergency disconnect | Arduino Uno + 2-ch optocoupler relay board (5 V coil, 30 A contact) | Sub-100 ms EStop on current anomaly | §5 |
| 7 | Current sensor | Allegro ACS758-100B (bi-directional hall) | Current monitoring fed into Arduino ADC | §5 |
| 8 | Voltage sense | Resistive divider 1:100 + INA219 I²C monitor | Divided pack voltage → Arduino | §5 |
| 9 | EVSE load bank | 5× incandescent 220 V / 250 W lamps in parallel | Dissipates PEV output during Attack 2 | §5 Attack 2 |
| 10 | Bench power (optional) | EA Elektro-Automatik EA-PSB 9750-20 2U | Supply when no vehicle is present for bring-up | §5 |

---

## Pin / signal table

### CCS Type-1 connector (inlet side)

| Pin | Name | What HotWire touches | HV? |
|-----|------|----------------------|-----|
| DC+ | Positive power | Load bank or vehicle battery (**untouched by HotWire firmware**) | **YES — up to 1 kV** |
| DC- | Negative power | Load bank or vehicle battery (untouched) | **YES** |
| CP | Control Pilot | PLC carrier — HomePlug modem CP/PE pair | No (±12 V PWM + PLC) |
| PE | Protective Earth | PLC return + chassis safety ground | No |
| PP | Proximity Pilot | Not used by HotWire (phase-2 feature) | No |

### RPi GPIO / USB

| RPi signal | Goes to | Purpose |
|-----------|---------|---------|
| `eth0` | Home network | Management, SSH, `apt` | 
| `eth1` (USB-Ethernet dongle) | QCA7005 modem | **This is the `--interface` arg for hw_check** |
| `/dev/ttyUSB0` | Arduino Uno | EStop trigger + sensor telemetry |
| GPIO 17 | Relay board IN1 | Software-commanded disconnect |

### Arduino Uno

| Arduino pin | Goes to | Signal |
|-------------|---------|--------|
| A0 | ACS758 OUT | Current reading (0-5 V, 20 mV/A scale) |
| SDA (A4) | INA219 SDA | I²C voltage monitor |
| SCL (A5) | INA219 SCL | I²C voltage monitor |
| D2 | Opto relay COIL | Sub-100 ms EStop (triggered on |I| > 60 A) |
| D13 | LED | Heartbeat + trigger status |

---

## Wiring for the two bench scenarios

### Scenario A — PEV emulator against a real commercial DC charger

```
[ Pi A + QCA7005 ] --- CCS connector --- [ Real charger station ]
                                                |
                                            DC+ / DC- feed
                                            into Pi A's "vehicle"
                                            resistive load bank
```

Used for: Attack 1 (EVCCID impersonation), Attack 2 preliminary
observations.

```bash
sudo python scripts/hw_check/run_all.py \
    --interface eth1 --role pev \
    --link-duration 15 --slac-budget 25 --v2g-budget 90
```

### Scenario B — EVSE emulator against a real vehicle

```
[ Real vehicle ] --- CCS connector --- [ Pi B + QCA7005 + load bank ]
                                                |
                                         Resistive load on DC pins
                                         to sink Attack 2's draw
```

Used for: Attack 2 live demonstration, vehicle-side defensive
capability characterization.

### Scenario C — Two-Pi bench (no charger, no vehicle)

```
[ Pi A (PEV role) ] ----- twisted-pair CP/PE ----- [ Pi B (EVSE role) ]
                                                           |
                                                   (load bank unused)
```

Each Pi runs `scripts/hw_check/run_all.py` with opposite `--role`
flags. SLAC pairs between the two, SDP completes, V2G session reaches
CurrentDemand over the DC pins carrying no power. All four phases of
hw_check can PASS in this setup.

This is the cheapest path to reproduce the paper's protocol-layer
claims without any vehicle or charger authorization.

---

## Calibration

To be added once a vehicle is available for cross-check. The paper
§5 calibration procedure — offset the ACS758 zero-current reading and
scale the INA219 divider to a reference 12 V supply — is straightforward
but the exact multipliers are instrument-specific. Current placeholder:

```python
# calibration.py (to be published)
CURRENT_ZERO_OFFSET = 2.510  # V at 0 A
CURRENT_SCALE = 20e-3        # V per A (from datasheet)
VOLTAGE_SCALE = 100.0        # 1:100 divider
```

---

## Reference schematics from upstream

The HotWire software layer was ported from **pyPLC** (GPL-3.0, uhi22).
pyPLC's original schematic covers the EVSE side comprehensively:

```
archive/legacy-evse/hardware/plc_evse/plc_evse_schematic_v1.pdf
```

For the PEV side, the same core (Pi 4 + QCA7005 + Arduino) is used
with inverted wiring: TCP client instead of server, resistive load on
DC+/DC- instead of bidirectional power supply. Full delta schematics
will land here once the paper's post-acceptance embargo lifts.

---

## References

- Paper §5 "Experimental Setup" — component rationale
- Paper §11 "Ethical Considerations" — why HV sides stay disconnected
  by default
- [SAFETY.md](../../SAFETY.md) — pre-energization checklist
- [../schematics/README.md](README.md) — embargo status + pyPLC pointer
