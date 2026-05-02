# HotWire hardware schematics — sourcing

HotWire's bench rig — described in paper §5 "Experimental Setup" —
is a Raspberry Pi 4 PEV / Windows EVSE host with QCA7420 HomePlug AV
modems, an Arduino-driven CCS coupler, and a resistive load bank for
Attack 2. The full **operator-grade build guide** is in
[`docs/hardware_design_guide.md`](../../docs/hardware_design_guide.md)
(537 lines: BOM, schematics, modem PIB programming, recovery
procedures, testing checklist).

## What HotWire's bench needs

- A Raspberry Pi 4 / Ubuntu 20.04 host
- A modified TP-Link HomePlug adapter built around the Qualcomm
  **QCA7005** Green PHY modem, connected over SPI / Ethernet
- CCS Type 1 connector + PLC coupling transformers (1:1, galvanic
  isolation) on the control-pilot and signal differential pairs
- 1.25 kW resistive load bank for Attack 2 (five 220 V / 250 W bulbs
  in parallel)
- Arduino-based current-sense and emergency-disconnect board with
  optocoupler-isolated relays and hall-effect current sensors

## Sourcing the schematics

HotWire's hardware layer is a delta over the open-source
[pyPLC](https://github.com/uhi22/pyPLC) project. The two upstream
KiCad schematics that cover the EVSE-side analog front-end and the
inverse PEV-side wiring are:

- **EVSE-side analog front-end** —
  [`pyPLC/hardware/plc_evse/plc_evse_schematic_v1.pdf`](https://github.com/uhi22/pyPLC/blob/master/hardware/plc_evse/plc_evse_schematic_v1.pdf)
  (1 nF coupling cap, 150 Ω TX impedance match, 1 kΩ CP pull-up to
  +12 V, BOM listed in `docs/hardware_design_guide.md` §5)
- **Arduino sense + relay firmware** —
  [`uhi22/dieter`](https://github.com/uhi22/dieter) (DieterHV +
  DieterLV sketches, 19200 baud serial — `RealHardwareInterface`
  speaks this dialect natively; see
  `hotwire/plc/real_hardware.py:_handle_line`)
- **PEV-side delta** — the PEV emulator reuses the same Pi + HomePlug
  adapter + Arduino core, with the inverse wiring (TCP client instead
  of server, resistive load on DC+/DC− instead of a bidirectional
  power supply). The wiring delta + a calibration procedure for the
  voltage / current sensing circuit are in
  `docs/hardware_design_guide.md` §3 and §13.

## Real-hardware evidence

If you want to confirm HotWire actually works end-to-end on this rig
without building one yourself, the captured pcaps + JSONL session
logs are shipped under
[`datasets/real_hw_traces/`](../../datasets/real_hw_traces/) (11
bundles covering A1, A2, multi-stage pause, abort, fuzz, stress, and
180 s sustained CurrentDemand without RSS / FD leak).
