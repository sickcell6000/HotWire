"""
Connection manager — tracks overall connection health across layers.

Each layer (ethernet link / modem / SLAC / SDP / TCP / application) reports
an ``Ok`` event which arms a timer. The overall ConnectionLevel reflects
the highest layer still within its timeout window.

Higher layers imply lower layers: if TCP is healthy we don't need modem
confirmation. This lets state machines stay silent until really needed.

Adapted from pyPLC's connMgr.py (GPL-3.0, uhi22).
"""
from __future__ import annotations

import sys
from typing import Callable

from .config import getConfigValueBool

CONNLEVEL_100_APPL_RUNNING = 100
CONNLEVEL_80_TCP_RUNNING = 80
CONNLEVEL_50_SDP_DONE = 50
CONNLEVEL_20_TWO_MODEMS_FOUND = 20
CONNLEVEL_15_SLAC_ONGOING = 15
CONNLEVEL_10_ONE_MODEM_FOUND = 10
CONNLEVEL_5_ETH_LINK_PRESENT = 5

CONNMGR_CYCLES_PER_SECOND = 33        # 30ms call interval
CONNMGR_TIMER_MAX = 5 * 33             # 5 s
CONNMGR_TIMER_MAX_10s = 10 * 33
CONNMGR_TIMER_MAX_15s = 15 * 33
CONNMGR_TIMER_MAX_20s = 20 * 33


class connMgr:
    def __init__(
        self,
        callbackAddToTrace: Callable[[str], None],
        callbackShowStatus: Callable[[str], None] | None = None,
    ) -> None:
        self.timerEthLink = 0
        self.timerModemLocal = 0
        self.timerModemRemote = 0
        self.timerSlac = 0
        self.timerSDP = 0
        self.timerTCP = 0
        self.timerAppl = 0
        self.ConnectionLevel = 0
        self.ConnectionLevelOld = 0
        self.cycles = 0
        self.addToTrace = callbackAddToTrace

    def getConnectionLevel(self) -> int:
        return self.ConnectionLevel

    def printDebugInfos(self) -> None:
        s = (
            "[CONNMGR] "
            + f"{self.timerEthLink} {self.timerModemLocal} {self.timerModemRemote} "
            + f"{self.timerSlac} {self.timerSDP} {self.timerTCP} {self.timerAppl}"
            + f" --> {self.ConnectionLevel}"
        )
        self.addToTrace(s)

    def mainfunction(self) -> None:
        # We don't actually check the ethernet link; assume it's always present.
        self.timerEthLink = 10

        for attr in (
            "timerEthLink", "timerModemLocal", "timerModemRemote",
            "timerSlac", "timerSDP", "timerTCP", "timerAppl",
        ):
            v = getattr(self, attr)
            if v > 0:
                setattr(self, attr, v - 1)

        # Compute the overall connection level from the highest healthy layer.
        if self.timerAppl > 0:
            self.ConnectionLevel = CONNLEVEL_100_APPL_RUNNING
        elif self.timerTCP > 0:
            self.ConnectionLevel = CONNLEVEL_80_TCP_RUNNING
        elif self.timerSDP > 0:
            self.ConnectionLevel = CONNLEVEL_50_SDP_DONE
        elif self.timerModemRemote > 0:
            self.ConnectionLevel = CONNLEVEL_20_TWO_MODEMS_FOUND
        elif self.timerSlac > 0:
            self.ConnectionLevel = CONNLEVEL_15_SLAC_ONGOING
        elif self.timerModemLocal > 0:
            self.ConnectionLevel = CONNLEVEL_10_ONE_MODEM_FOUND
        elif self.timerEthLink > 0:
            self.ConnectionLevel = CONNLEVEL_5_ETH_LINK_PRESENT
        else:
            self.ConnectionLevel = 0

        if self.ConnectionLevelOld != self.ConnectionLevel:
            self.addToTrace(
                f"[CONNMGR] ConnectionLevel changed from {self.ConnectionLevelOld} to {self.ConnectionLevel}"
            )
            if self.ConnectionLevelOld == 100 and self.ConnectionLevel < 100:
                # Charging session ended — optionally terminate the process.
                if getConfigValueBool("exit_on_session_end"):
                    self.addToTrace("[CONNMGR] Terminating the application.")
                    sys.exit(0)
            self.ConnectionLevelOld = self.ConnectionLevel

        if (self.cycles % 33) == 0:
            self.printDebugInfos()
        self.cycles += 1

    def ModemFinderOk(self, numberOfFoundModems: int) -> None:
        if numberOfFoundModems >= 1:
            self.timerModemLocal = CONNMGR_TIMER_MAX
        if numberOfFoundModems >= 2:
            self.timerModemRemote = CONNMGR_TIMER_MAX_10s

    def SlacOk(self) -> None:
        # SetKey was sent to local modem → restart. Allow time for remote
        # modem to re-pair before assuming failure.
        self.timerSlac = CONNMGR_TIMER_MAX_20s

    def SdpOk(self) -> None:
        self.timerSDP = CONNMGR_TIMER_MAX

    def TcpOk(self) -> None:
        self.timerTCP = CONNMGR_TIMER_MAX_10s

    def ApplOk(self, time_in_seconds: int = 10) -> None:
        """Application layer confirms communication is alive for N seconds."""
        self.timerAppl = time_in_seconds * CONNMGR_CYCLES_PER_SECOND


if __name__ == "__main__":
    print("Testing hotwire.core.conn_mgr...")
    cm = connMgr(print)
    print(f"initial level = {cm.getConnectionLevel()}")
    cm.SlacOk()
    cm.mainfunction()
    print(f"after SlacOk, level = {cm.getConnectionLevel()}")
