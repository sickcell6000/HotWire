# HotWire hardware schematics — status

**This directory is a placeholder.** The paper's §5 "Experimental Setup"
describes:

- A Raspberry Pi 4 / Ubuntu 20.04 host
- A modified TP-Link HomePlug adapter built around the Qualcomm
  **QCA7005** Green PHY modem, connected over SPI / Ethernet
- CCS Type 1 connector + PLC coupling transformers (1:1, galvanic
  isolation) on the control-pilot and signal differential pairs
- 1.25 kW resistive load bank for Attack 2 (five 220 V / 250 W bulbs
  in parallel)
- Arduino-based current-sense and emergency-disconnect board with
  optocoupler-isolated relays and hall-effect current sensors

The circuit-level schematics referenced in the paper are not included
in the current HotWire release. They will land here before the
post-acceptance public release.

In the meantime, the pyPLC project upstream — from which HotWire's
software layer is ported — publishes a substantially similar EVSE-side
schematic. See:

```
archive/legacy-evse/hardware/plc_evse/plc_evse_schematic_v1.pdf
```

The PEV-side emulator uses the same core (Pi 4 + HomePlug adapter +
Arduino) with an additional resistive load bank and a CCS-1 connector
wired to the DC pins. We will publish the delta schematics and a
calibration procedure for the voltage / current sensing circuit once
the coordinated-disclosure embargo described in the paper's Ethical
Considerations section has expired.

If you are reproducing HotWire's experiments now and need build
details, the pyPLC schematic plus its `archive/legacy-evse/doc/`
documentation is enough to get a working EVSE-side emulator; the PEV
side is the same hardware with the inverse wiring (TCP client instead
of server, resistive load on DC+/DC- instead of bidirectional power
supply).
