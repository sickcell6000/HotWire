"""
Network-interface enumerator + scorer.

Central entry point: :func:`list_interfaces` returns a list of
:class:`NetInterface` dataclasses sorted best-first by a score that
tries to estimate "how likely is this a PLC modem on the way to a
charger".

The scorer never phones home and never writes any state. It combines:

* ``psutil.net_if_addrs()`` — address families, MAC, IPv4, IPv6
* ``psutil.net_if_stats()`` — isup, MTU, negotiated speed
* Linux: ``/sys/class/net/<name>/carrier`` for real PLC link state
* Windows: ``ipconfig /all`` parsed for human-readable descriptions

Every probe that fails falls back to a sentinel (None / False / empty
list) so the enumerator never raises on flaky hosts.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional


# --- dataclass --------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class NetInterface:
    """One network interface and its enumerated attributes.

    The ``name`` is what the OS uses internally (``"eth1"`` on Linux,
    a ``\\Device\\NPF_{GUID}``-style string on Windows — although on
    Windows psutil usually returns a friendly name, which can still be
    passed to pcap after prefixing ``\\Device\\NPF_``).

    ``reasons`` lists the scoring rules that fired (positive or
    negative), so the GUI can show a tooltip like:
    ``["+10 UP", "+20 QCA OUI", "+5 IPv6 link-local only"]``.
    """
    name: str
    display_name: str
    mac: str
    mtu: int
    is_up: bool
    has_carrier: Optional[bool]
    speed_mbps: Optional[int]
    ipv4: list[str]
    ipv6: list[str]
    score: int
    reasons: list[str]

    def short_label(self) -> str:
        """Compact label the picker combo box shows."""
        hint = _score_hint(self.score)
        bits = [self.name, f"score={self.score} {hint}"]
        if self.has_carrier is True:
            bits.append("carrier")
        elif self.is_up:
            bits.append("up")
        else:
            bits.append("down")
        if self.speed_mbps:
            bits.append(f"{self.speed_mbps} Mbps")
        return f"{bits[0]}  ({', '.join(bits[1:])})"

    def tooltip(self) -> str:
        """Multi-line HTML tooltip shown on hover."""
        lines = [f"<b>{self.name}</b>"]
        if self.display_name and self.display_name != self.name:
            lines.append(f"<i>{self.display_name}</i>")
        lines.append(f"MAC: <tt>{self.mac or '?'}</tt>")
        lines.append(f"MTU: {self.mtu}")
        if self.has_carrier is True:
            lines.append("Carrier: <span style='color: #2E7D32;'>up</span>")
        elif self.has_carrier is False:
            lines.append("Carrier: <span style='color: #C62828;'>down</span>")
        if self.speed_mbps:
            lines.append(f"Speed: {self.speed_mbps} Mbps")
        if self.ipv4:
            lines.append(f"IPv4: {', '.join(self.ipv4)}")
        if self.ipv6:
            lines.append(f"IPv6: {', '.join(self.ipv6)}")
        lines.append(f"<b>Score:</b> {self.score}")
        if self.reasons:
            lines.append("<br>".join(f"&nbsp;• {r}" for r in self.reasons))
        return "<br>".join(lines)


# --- scoring rubric ---------------------------------------------------


# Historical Atheros / Qualcomm Atheros OUI prefixes. Not exhaustive —
# the goal is "good enough signal that a hit is probably a PLC modem".
_QCA_OUIS = frozenset({
    "00:b0:52", "00:80:25", "00:15:f2", "00:23:04", "00:26:86",
    "fc:a8:42", "00:03:7f", "44:a5:6e",
})

_VIRTUAL_HINTS = (
    "loopback", "docker", "veth", "vmware", "virtual",
    "vEthernet", "tunnel", "bluetooth", "wifi", "wi-fi", "wireless",
    "teredo", "isatap", "pseudo",
)

_LIKELY_WIRED_PREFIXES = ("eth", "enp", "enx", "en0", "eno", "ens")


def _score(ni_data: dict) -> tuple[int, list[str]]:
    """Run the rubric. Returns (total, reasons)."""
    score = 0
    reasons: list[str] = []

    def award(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(f"{points:+d}  {reason}")

    name = ni_data["name"]
    lname = name.lower()
    display = (ni_data.get("display_name") or "").lower()
    mac = ni_data.get("mac", "").lower()
    is_up = bool(ni_data.get("is_up"))
    has_carrier = ni_data.get("has_carrier")
    speed = ni_data.get("speed_mbps")
    mtu = ni_data.get("mtu") or 0
    ipv4 = ni_data.get("ipv4") or []
    ipv6 = ni_data.get("ipv6") or []

    # Hard negatives first — loopback + virtual hints dominate.
    if lname == "lo" or lname == "loopback" or "loopback" in display:
        award(-100, "loopback")
    for hint in _VIRTUAL_HINTS:
        if hint in lname or hint in display:
            award(-50, f"matches virtual keyword {hint!r}")
            break

    # Positive signals.
    if is_up:
        award(+10, "interface is UP")
    else:
        award(-5, "interface is DOWN")

    if has_carrier is True:
        award(+15, "carrier detected")
    elif has_carrier is False and is_up:
        award(-3, "UP but no carrier (cable?)")

    if speed in (10, 100):
        award(+5, f"{speed} Mbps (HomePlug AV typical)")
    elif speed and speed >= 1000:
        award(-3, f"{speed} Mbps (likely office LAN)")

    if mac and _oui_of(mac) in _QCA_OUIS:
        award(+20, "MAC OUI matches Qualcomm Atheros (QCA)")

    for prefix in _LIKELY_WIRED_PREFIXES:
        if lname.startswith(prefix):
            award(+3, f"name matches wired prefix {prefix!r}")
            break

    if mtu == 1500:
        award(+2, "MTU 1500 (standard ethernet)")
    elif mtu > 0 and mtu < 1500:
        award(-4, f"MTU {mtu} < 1500 (HomePlug will fragment)")

    # IPv4 private range? Probably office LAN.
    private_ipv4 = False
    for addr in ipv4:
        try:
            if ipaddress.IPv4Address(addr).is_private:
                private_ipv4 = True
                break
        except ValueError:
            continue
    if private_ipv4:
        award(-5, "IPv4 private address (likely office LAN)")

    # IPv6: link-local only (no global) is a plus (PLC modems don't get public IPv6).
    ipv6_global = any(
        _is_ipv6_global(addr) for addr in ipv6
    )
    if ipv6 and not ipv6_global:
        award(+5, "IPv6 link-local only")

    return score, reasons


def _oui_of(mac: str) -> str:
    """Return the first three octets in canonical lowercase colon form."""
    norm = mac.lower().replace("-", ":")
    parts = norm.split(":")
    if len(parts) < 3:
        return ""
    return ":".join(parts[:3])


def _is_ipv6_global(addr: str) -> bool:
    """True if ``addr`` is a routable global IPv6 address."""
    try:
        ip = ipaddress.IPv6Address(addr.split("%", 1)[0])
        return ip.is_global
    except ValueError:
        return False


def _score_hint(score: int) -> str:
    """One-word summary of a numeric score."""
    if score >= 30:
        return "★ best"
    if score >= 15:
        return "good"
    if score >= 0:
        return "usable"
    if score >= -50:
        return "unlikely"
    return "hidden"


# --- platform probes --------------------------------------------------


def _linux_read_sys(name: str, attr: str) -> Optional[str]:
    path = Path(f"/sys/class/net/{name}/{attr}")
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _linux_carrier(name: str) -> Optional[bool]:
    val = _linux_read_sys(name, "carrier")
    if val is None:
        return None
    return val == "1"


def _linux_speed(name: str) -> Optional[int]:
    val = _linux_read_sys(name, "speed")
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def _windows_display_names() -> dict[str, str]:
    """Best-effort: map psutil name → adapter description via ipconfig /all.

    Returns an empty dict if ipconfig fails or is unavailable. The output
    is locale-dependent, so we don't try to parse it rigorously — we
    just associate the last-seen adapter description with its NIC name.
    """
    try:
        out = subprocess.run(
            ["ipconfig.exe", "/all"],
            capture_output=True, text=True, timeout=5, encoding="ansi",
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if not out.stdout:
        return {}

    display: dict[str, str] = {}
    current_name: Optional[str] = None
    desc_re = re.compile(r"Description[^:]*:\s*(.+)")
    for line in out.stdout.splitlines():
        # Adapter header: ends with ":" and not indented, skipping empty.
        stripped = line.rstrip()
        if stripped and not line.startswith(" ") and stripped.endswith(":"):
            # Often e.g. "Ethernet adapter Local Area Connection:"
            name_match = stripped[:-1]
            parts = name_match.split(" adapter ", 1)
            if len(parts) == 2:
                current_name = parts[1].strip()
        elif current_name:
            m = desc_re.search(line)
            if m:
                display[current_name] = m.group(1).strip()
                current_name = None
    return display


# --- public API -------------------------------------------------------


def list_interfaces() -> list[NetInterface]:
    """Enumerate all NICs, score them, return best-first."""
    try:
        import psutil
    except ImportError:
        return []

    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:                                            # noqa: BLE001
        return []

    display_map: dict[str, str] = {}
    if sys.platform == "win32":
        display_map = _windows_display_names()

    out: list[NetInterface] = []
    for name, addr_list in addrs.items():
        mac = ""
        ipv4: list[str] = []
        ipv6: list[str] = []
        for addr in addr_list:
            family = getattr(addr, "family", None)
            if family == socket.AF_INET:
                if addr.address:
                    ipv4.append(addr.address)
            elif family == socket.AF_INET6:
                if addr.address:
                    ipv6.append(addr.address)
            elif family == getattr(psutil, "AF_LINK", -1):
                if addr.address:
                    mac = addr.address.lower().replace("-", ":")

        st = stats.get(name)
        is_up = bool(getattr(st, "isup", False)) if st else False
        mtu = int(getattr(st, "mtu", 0)) if st else 0
        speed = getattr(st, "speed", 0) if st else 0
        speed_val: Optional[int] = int(speed) if speed else None

        has_carrier: Optional[bool] = None
        if sys.platform.startswith("linux"):
            has_carrier = _linux_carrier(name)
            lspeed = _linux_speed(name)
            if lspeed and not speed_val:
                speed_val = lspeed

        display = display_map.get(name, "")

        data = {
            "name": name,
            "display_name": display,
            "mac": mac,
            "mtu": mtu,
            "is_up": is_up,
            "has_carrier": has_carrier,
            "speed_mbps": speed_val,
            "ipv4": ipv4,
            "ipv6": ipv6,
        }
        score, reasons = _score(data)

        out.append(NetInterface(
            name=name,
            display_name=display,
            mac=mac,
            mtu=mtu,
            is_up=is_up,
            has_carrier=has_carrier,
            speed_mbps=speed_val,
            ipv4=ipv4,
            ipv6=ipv6,
            score=score,
            reasons=reasons,
        ))

    out.sort(key=lambda ni: ni.score, reverse=True)
    return out
