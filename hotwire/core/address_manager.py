"""
Address manager — keeps/provides/finds MAC and IPv6 addresses.

In simulation mode, link-local IPv6 is replaced with loopback so two
processes on one host can talk via TCP without a real PLC modem.

Adapted from pyPLC's addressManager.py (GPL-3.0, uhi22).
"""
from __future__ import annotations

import ipaddress
import os
import subprocess
import sys

from ..helpers import prettyMac, twoCharHex
from .config import getConfigValue, getConfigValueBool

MAC_LAPTOP = [0x00, 0xE0, 0x4C, 0x36, 0x28, 0xD2]  # fallback for Windows

# Loopback used in pure-software simulation mode.
SIMULATION_IPV6 = "::1"


class addressManager:
    def __init__(self, isSimulationMode: int = 0) -> None:
        self.isSimulationMode = isSimulationMode
        self.localIpv6Addresses: list[str] = []
        self.findLocalMacAddress()

        if self.isSimulationMode:
            # In simulation we don't need link-local addresses.
            # Both EVSE and PEV bind to ::1 and talk over loopback TCP.
            self.localIpv6Address = SIMULATION_IPV6
            self.localIpv6Addresses = [SIMULATION_IPV6]
            print("[addressManager] Simulation mode: using ::1 loopback")
        else:
            self.findLinkLocalIpv6Address()

        self.pevIp = ""
        self.SeccIp = ""
        self.SeccTcpPort = 15118  # default, overwritten by SDP when we are PEV
        self.evseMacIsUpdated = False
        self.evseMac = [0, 0, 0, 0, 0, 0]

    def findLinkLocalIpv6Address(self) -> None:
        """Find a link-local fe80::/10 address on the selected ethernet interface."""
        ba = bytearray(6)
        foundAddresses: list[str] = []
        if os.name == "nt":
            # Windows: resolve the configured interface to its link-local
            # IPv6 explicitly instead of scraping every fe80:: in
            # ipconfig. Grabbing the first fe80:: on the machine was
            # what forced operators to disable all other NICs — now we
            # ask Get-NetAdapter for the NIC whose InterfaceGuid matches
            # the NPF GUID in eth_windows_interface_name, then pull the
            # matching link-local via Get-NetIPAddress.
            target_npf = ""
            try:
                target_npf = getConfigValue("eth_windows_interface_name")
            except Exception:                                     # noqa: BLE001
                pass

            # Extract GUID between {...} in the NPF path
            guid = ""
            if target_npf:
                i = target_npf.find("{")
                j = target_npf.find("}", i)
                if i != -1 and j != -1:
                    guid = target_npf[i:j + 1]

            ps_cmd = ""
            if guid:
                ps_cmd = (
                    "$g='" + guid + "';"
                    "$n=(Get-NetAdapter|?{$_.InterfaceGuid -eq $g}).Name;"
                    "if($n){"
                    " (Get-NetIPAddress -InterfaceAlias $n -AddressFamily IPv6"
                    " -ErrorAction SilentlyContinue|"
                    "  ?{$_.IPAddress -like 'fe80::*'}|"
                    "  Select -ExpandProperty IPAddress)"
                    "}"
                )
            if ps_cmd:
                try:
                    result = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
                        capture_output=True, text=True, timeout=5,
                    )
                    for line in (result.stdout or "").splitlines():
                        line = line.strip()
                        if line.lower().startswith("fe80::"):
                            # Drop %iface suffix if powershell ever adds it
                            foundAddresses.append(line.split("%", 1)[0])
                except (OSError, subprocess.TimeoutExpired):
                    pass

            # Fallback for hosts where PowerShell/NetAdapter is not
            # cooperative — scan ipconfig but accept any fe80::. We
            # still warn about multi-adapter ambiguity below.
            if not foundAddresses:
                result = subprocess.run(
                    ["ipconfig.exe"],
                    capture_output=True, text=True, encoding="ansi",
                )
                if len(result.stderr) > 0:
                    print(result.stderr)
                else:
                    for line in result.stdout.split("\n"):
                        if line.find("IPv6") > 0:
                            k = line.find(" fe80::")
                            if k > 0:
                                foundAddresses.append(line[k + 1 :])
        else:
            # Linux: parse `ip addr` output for the configured interface
            result = subprocess.run(["ip", "addr"], capture_output=True, text=True)
            if len(result.stderr) > 0:
                print(result.stderr)
            else:
                blInTheEthernetChapter = 0
                lines = result.stdout.split("\n")
                for line in lines:
                    if line[0:1] != " ":
                        sFind = ": " + getConfigValue("eth_interface")
                        blInTheEthernetChapter = 1 if line.find(sFind) > 0 else 0
                    else:
                        if (
                            blInTheEthernetChapter == 1
                            and line.find("  inet6") > 0
                            and line.find(" scope link") > 0
                        ):
                            k = line.find(" fe80::")
                            if k > 0:
                                sIpWithText = line[k + 1 :]
                                x = sIpWithText.find(" ")
                                sIp = sIpWithText[0:x]
                                x = sIp.find("/")
                                if x > 0:
                                    sIp = sIp[0:x]
                                foundAddresses.append(sIp)
                        if (
                            blInTheEthernetChapter == 1
                            and line.find(" link/ether") > 0
                        ):
                            k = line.find("link/ether ")
                            strMac = line[k + 11 : k + 28].replace(":", "")
                            if len(strMac) != 12:
                                print(
                                    "[addressManager] ERROR: invalid MAC length "
                                    + str(len(strMac))
                                )
                            else:
                                for i in range(0, 6):
                                    ba[i] = int(strMac[2 * i : 2 * i + 2], 16)
                                self.localMac = ba
                                print(
                                    "[addressManager] local MAC = " + prettyMac(self.localMac)
                                )

        print(f"[addressManager] Found {len(foundAddresses)} link-local IPv6 addresses.")
        for a in foundAddresses:
            print(a)
        self.localIpv6Addresses = foundAddresses

        if len(foundAddresses) == 0:
            print("[addressManager] ERROR: No local IPv6 address found.")
            self.localIpv6Address = "localhost"
            if getConfigValueBool("exit_if_no_local_link_address_is_found"):
                print("Exiting — cannot continue without IPv6 address")
                sys.exit(1)
        else:
            self.localIpv6Address = foundAddresses[0]
            if len(foundAddresses) > 1:
                print(
                    "[addressManager] Warning: multiple IPv6 addresses. Using "
                    + foundAddresses[0]
                )
        print(f"[addressManager] Local IPv6 = {self.localIpv6Address}")

    def findLocalMacAddress(self) -> None:
        """On Windows use a static fallback; on Linux MAC is set by
        findLinkLocalIpv6Address(). Simulation mode on Linux never
        goes through that path, so we seed a static fallback here too
        — otherwise ``self.localMac`` is missing when callers ask for
        the EVCCID (happens in Docker CI, where no real eth interface
        exists)."""
        if os.name == "nt":
            self.localMac = MAC_LAPTOP
            print(
                "[addressManager] local MAC = "
                + prettyMac(self.localMac)
                + " (static Windows fallback)"
            )
        else:
            # Pre-seed on Linux so simulation mode has something. Real
            # hardware runs will overwrite this in findLinkLocalIpv6Address.
            self.localMac = MAC_LAPTOP

    # ---- setters for remote peer addresses ----

    def setPevMac(self, pevMac: list[int] | bytearray) -> None:
        self.pevMac = pevMac
        print("[addressManager] pev MAC = " + prettyMac(self.pevMac))

    def setEvseMac(self, evseMac: list[int] | bytearray) -> None:
        self.evseMac = evseMac
        self.evseMacIsUpdated = True
        print("[addressManager] evse MAC = " + prettyMac(self.evseMac))

    def getEvseMacAsStringAndClearUpdateFlag(self) -> str:
        self.evseMacIsUpdated = False
        return prettyMac(self.evseMac)

    def isEvseMacNew(self) -> bool:
        return self.evseMacIsUpdated

    # ---- IPv6 scope / interface helpers ----

    def getInterfaceName(self) -> str:
        """Config-selected ethernet interface name, e.g. ``eth0``.

        Used by SDP and any other code that needs an interface index
        for link-local IPv6 (``fe80::...%eth0``). Returns an empty string
        if unset, which is acceptable in simulation mode.
        """
        try:
            return getConfigValue("eth_interface") or ""
        except Exception:                                       # noqa: BLE001
            return ""

    def getScopeId(self) -> int:
        """Return the IPv6 scope id for the configured interface.

        On Linux this is ``if_nametoindex(eth0)``. On Windows link-local
        addresses embed the interface index in the address itself
        (``fe80::...%N``), so we parse it out of ``localIpv6Address``
        when available; a return of 0 lets ``socket`` pick the default
        interface, which is the correct behaviour on loopback.
        """
        if self.isSimulationMode:
            return 0
        iface = self.getInterfaceName()
        if iface:
            import socket
            try:
                return socket.if_nametoindex(iface)
            except (OSError, AttributeError):
                pass
        # Windows: extract the "%N" suffix from the first link-local addr.
        for addr in self.localIpv6Addresses:
            if "%" in addr:
                try:
                    return int(addr.split("%", 1)[1])
                except (ValueError, IndexError):
                    pass
        return 0

    def getLinkLocalAddressWithoutScope(self) -> str:
        """Return the first link-local IPv6 with any ``%N`` suffix stripped.

        ``socket.bind`` on Windows wants the scope via the 4-tuple's
        ``scope_id`` field, not embedded in the string.
        """
        if not self.localIpv6Address:
            return ""
        addr = self.localIpv6Address
        if "%" in addr:
            addr = addr.split("%", 1)[0]
        return addr

    def setPevIp(self, pevIp: bytearray | bytes | str) -> None:
        if isinstance(pevIp, (bytearray, bytes)):
            if len(pevIp) != 16:
                print(f"[addressManager] ERROR: invalid IPv6 length {len(pevIp)}")
                return
            s = ""
            for i in range(0, 16):
                s = s + twoCharHex(pevIp[i])
                if (i % 2) == 1 and i != 15:
                    s = s + ":"
            self.pevIp = s.lower()
        else:
            self.pevIp = pevIp
        print(f"[addressManager] pev IP = {self.pevIp}")

    def setSeccIp(self, SeccIp: bytearray | bytes | str) -> None:
        if isinstance(SeccIp, (bytearray, bytes)):
            if len(SeccIp) != 16:
                print(f"[addressManager] ERROR: invalid IPv6 length {len(SeccIp)}")
                return
            s = ""
            for i in range(0, 16):
                s = s + twoCharHex(SeccIp[i])
                if (i % 2) == 1 and i != 15:
                    s = s + ":"
            self.SeccIp = s.lower()
        else:
            self.SeccIp = SeccIp
        print(f"[addressManager] secc IP = {self.SeccIp}")

    def getSeccIp(self) -> str:
        return self.SeccIp

    def setSeccTcpPort(self, port: int) -> None:
        self.SeccTcpPort = port
        print(f"[addressManager] secc TCP port = {self.SeccTcpPort}")

    def getSeccTcpPort(self) -> int:
        return self.SeccTcpPort

    # ---- accessors for local address ----

    def getLocalMacAddress(self) -> list[int] | bytearray:
        return self.localMac

    def getLocalMacAsTwelfCharString(self) -> str:
        """Return local MAC as 12-char lowercase hex (no separators)."""
        s = ""
        for i in range(0, 6):
            s = s + twoCharHex(self.localMac[i])
        return s

    def getLinkLocalIpv6Address(self, resulttype: str = "string"):
        if resulttype == "string":
            return self.localIpv6Address
        if resulttype == "bytearray":
            s = self.localIpv6Address.partition("%")[0]
            s = ipaddress.IPv6Address(s).exploded.replace(":", "")
            ba = bytearray(16)
            if len(s) != 32:
                print(f"[addressManager] ERROR: invalid IPv6 string length {len(s)}")
                return ba
            for i in range(0, 16):
                ba[i] = int(s[2 * i : 2 * i + 2], 16)
            return ba
        return None


if __name__ == "__main__":
    print("Testing hotwire.core.address_manager...")
    am = addressManager(isSimulationMode=1)
    print(f"IPv6 = {am.getLinkLocalIpv6Address()}")
    print(f"MAC = {am.getLocalMacAsTwelfCharString()}")
