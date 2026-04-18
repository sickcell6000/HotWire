"""
HotWire — DIN 70121 / ISO 15118-2 EV charging security testbed.

A bi-directional V2G protocol testing platform supporting:
- Malicious EVSE emulator (for EVCCID harvesting, forced discharge)
- Rogue EV emulator (for Autocharge impersonation)
- Fully configurable request/response parameters with pause/modify/send control
- Pure-software simulation mode for reproducible testing without PLC hardware

Built upon pyPLC (GPL-3.0) by uhi22 — see ATTRIBUTION.md.
"""

__version__ = "1.0.0"
__author__ = "Kuan Yu Chen"
__license__ = "GPL-3.0"
