"""
Registry of preflight hardware-readiness checks.

Each check is a small pure function that returns a :class:`CheckResult`.
The :func:`register_check` decorator registers it into the module-level
``CHECKS`` list so the :class:`PreflightRunner` (and any other caller)
can iterate them.

Design goals:

* **Cheap to run** — the whole sweep should complete in < 5 seconds on
  the target hardware. Any network call is bounded by a 2 second
  timeout; no blocking I/O against arbitrary hosts.
* **No side effects** — checks never modify state. They only read.
* **Remediation included** — every FAIL carries a one-liner the operator
  can copy-paste into a shell.

Checks categorised by platform scope via a ``{"linux", "windows"}``
set. Runner skips the check if the current OS isn't in the set.
"""
from __future__ import annotations

import dataclasses
import enum
import importlib
import ipaddress
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional


# --- result types -----------------------------------------------------


class CheckStatus(enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"

    @property
    def symbol(self) -> str:
        return {
            CheckStatus.PASS: "[PASS]",
            CheckStatus.FAIL: "[FAIL]",
            CheckStatus.WARN: "[WARN]",
            CheckStatus.SKIP: "[SKIP]",
        }[self]


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """One check's outcome.

    Attributes
    ----------
    name
        Human-readable check label, shown in reports and the wizard UI.
    status
        PASS / FAIL / WARN / SKIP.
    observed
        What the system actually shows, e.g. ``"MTU = 576"``.
    expected
        What the check wants, e.g. ``"MTU >= 1500"``.
    remediation
        Single-line shell command or note the operator can apply to
        fix a FAIL or address a WARN. Empty for PASS/SKIP.
    platforms
        Where this check makes sense. Everything not in this set gets
        automatically SKIP'd.
    elapsed_ms
        Wall-clock time the check took; useful to spot slow probes.
    """
    name: str
    status: CheckStatus
    observed: str = ""
    expected: str = ""
    remediation: str = ""
    platforms: frozenset[str] = frozenset({"linux", "windows"})
    elapsed_ms: float = 0.0


@dataclasses.dataclass(frozen=True)
class Check:
    """A registered check — wraps the callable with metadata."""
    name: str
    platforms: frozenset[str]
    fn: Callable[..., CheckResult]
    category: str = "general"         # general / linux / windows / system


CHECKS: list[Check] = []


def register_check(
    name: str,
    *,
    platforms: frozenset[str] | set[str] = frozenset({"linux", "windows"}),
    category: str = "general",
) -> Callable[[Callable[..., CheckResult]], Callable[..., CheckResult]]:
    """Decorator: register a check function under the given name."""
    platforms_fs = frozenset(platforms)

    def wrap(fn: Callable[..., CheckResult]) -> Callable[..., CheckResult]:
        CHECKS.append(Check(
            name=name, platforms=platforms_fs, fn=fn, category=category,
        ))
        return fn

    return wrap


# --- small helpers ---------------------------------------------------


def _timed_result(
    name: str,
    start: float,
    status: CheckStatus,
    observed: str = "",
    expected: str = "",
    remediation: str = "",
    platforms: frozenset[str] | None = None,
) -> CheckResult:
    elapsed_ms = (time.monotonic() - start) * 1000.0
    return CheckResult(
        name=name,
        status=status,
        observed=observed,
        expected=expected,
        remediation=remediation,
        platforms=platforms or frozenset({"linux", "windows"}),
        elapsed_ms=round(elapsed_ms, 2),
    )


def _which(binary: str) -> Optional[str]:
    return shutil.which(binary)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


# =====================================================================
# GENERAL (both Linux and Windows)
# =====================================================================


@register_check("Python version", category="general")
def check_python_version(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    v = sys.version_info
    observed = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 9):
        return _timed_result(
            "Python version", t0, CheckStatus.PASS,
            observed=observed, expected=">= 3.9",
        )
    return _timed_result(
        "Python version", t0, CheckStatus.FAIL,
        observed=observed,
        expected=">= 3.9",
        remediation="Install Python 3.9 or newer from python.org",
    )


@register_check("OpenV2G codec binary", category="general")
def check_openv2g_binary(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    codec = _repo_root() / "hotwire" / "exi" / "codec" / "OpenV2G.exe"
    if not codec.exists():
        alt = codec.with_suffix("")
        if alt.exists():
            codec = alt
    if not codec.exists():
        return _timed_result(
            "OpenV2G codec binary", t0, CheckStatus.FAIL,
            observed="not found",
            expected="hotwire/exi/codec/OpenV2G[.exe] exists",
            remediation="python vendor/build_openv2g.py",
        )
    # Architecture sanity — compare against host machine().
    arch_info = _binary_architecture(codec)
    try:
        out = subprocess.run(
            [str(codec), "EDa_1_5A5A4445464C54"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return _timed_result(
            "OpenV2G codec binary", t0, CheckStatus.FAIL,
            observed=f"failed to invoke ({e}); arch={arch_info}",
            expected="codec returns a 'result' JSON field",
            remediation="Rebuild: python vendor/build_openv2g.py",
        )
    if out.returncode != 0 or "result" not in out.stdout:
        return _timed_result(
            "OpenV2G codec binary", t0, CheckStatus.FAIL,
            observed=f"rc={out.returncode} arch={arch_info}",
            expected="rc=0 with 'result' field",
            remediation=(
                "Rebuild for this host — the shipped binary may be for "
                "another architecture. Run: python vendor/build_openv2g.py"
            ),
        )
    return _timed_result(
        "OpenV2G codec binary", t0, CheckStatus.PASS,
        observed=f"{codec.name} ({arch_info})",
        expected="exists + executes + emits JSON",
    )


@register_check("Packet capture tool", category="general")
def check_capture_tool(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    for tool in ("dumpcap", "tcpdump"):
        if _which(tool):
            return _timed_result(
                "Packet capture tool", t0, CheckStatus.PASS,
                observed=f"{tool} available on PATH",
                expected="tcpdump or dumpcap in PATH",
            )
    return _timed_result(
        "Packet capture tool", t0, CheckStatus.WARN,
        observed="neither tcpdump nor dumpcap found",
        expected="tcpdump or dumpcap in PATH",
        remediation=(
            "Linux: sudo apt install tcpdump   "
            "or   sudo apt install wireshark-common"
        ) if sys.platform.startswith("linux") else (
            "Windows: install Wireshark (bundles dumpcap) from wireshark.org"
        ),
    )


@register_check("psutil library available", category="general")
def check_psutil(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    try:
        import psutil  # noqa: F401
        import psutil as _ps
        return _timed_result(
            "psutil library available", t0, CheckStatus.PASS,
            observed=f"psutil {_ps.__version__}",
            expected=">= 5.9.0",
        )
    except ImportError:
        return _timed_result(
            "psutil library available", t0, CheckStatus.FAIL,
            observed="import failed",
            expected=">= 5.9.0 installed",
            remediation="pip install psutil>=5.9.0",
        )


@register_check("hotwire package importable", category="general")
def check_hotwire_importable(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    try:
        importlib.import_module("hotwire")
        importlib.import_module("hotwire.fsm.pause_controller")
        importlib.import_module("hotwire.core.worker")
        return _timed_result(
            "hotwire package importable", t0, CheckStatus.PASS,
            observed="hotwire + hotwire.fsm + hotwire.core import OK",
            expected="all three core subpackages importable",
        )
    except Exception as e:                                       # noqa: BLE001
        return _timed_result(
            "hotwire package importable", t0, CheckStatus.FAIL,
            observed=f"import error: {e}",
            expected="all three core subpackages importable",
            remediation=(
                "Run `pip install -r requirements.txt` and verify the "
                "working directory is the repo root"
            ),
        )


@register_check("Disk space for runs/", category="general")
def check_disk_space(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    try:
        import psutil
    except ImportError:
        return _timed_result(
            "Disk space for runs/", t0, CheckStatus.SKIP,
            observed="psutil not installed",
            expected=">= 1 GB free on runs/ partition",
        )
    target = _repo_root()
    try:
        du = psutil.disk_usage(str(target))
    except OSError as e:
        return _timed_result(
            "Disk space for runs/", t0, CheckStatus.FAIL,
            observed=f"disk_usage failed: {e}",
            expected=">= 1 GB free",
        )
    gb_free = du.free / (1024 ** 3)
    if gb_free >= 1.0:
        return _timed_result(
            "Disk space for runs/", t0, CheckStatus.PASS,
            observed=f"{gb_free:.1f} GB free",
            expected=">= 1 GB free",
        )
    return _timed_result(
        "Disk space for runs/", t0, CheckStatus.WARN,
        observed=f"{gb_free:.2f} GB free",
        expected=">= 1 GB free",
        remediation="Clean up runs/ older than 7 days or move to a larger disk",
    )


# =====================================================================
# LINUX (capabilities / interface / network)
# =====================================================================


@register_check(
    "Root or CAP_NET_RAW",
    platforms={"linux"},
    category="linux",
)
def check_cap_net_raw(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    euid = os.geteuid() if hasattr(os, "geteuid") else -1
    if euid == 0:
        return _timed_result(
            "Root or CAP_NET_RAW", t0, CheckStatus.PASS,
            observed="euid=0 (root)",
            expected="root or CAP_NET_RAW",
            platforms=frozenset({"linux"}),
        )
    cap = _linux_has_cap_net_raw()
    if cap:
        return _timed_result(
            "Root or CAP_NET_RAW", t0, CheckStatus.PASS,
            observed="CAP_NET_RAW granted on effective set",
            expected="root or CAP_NET_RAW",
            platforms=frozenset({"linux"}),
        )
    return _timed_result(
        "Root or CAP_NET_RAW", t0, CheckStatus.FAIL,
        observed=f"euid={euid}, CAP_NET_RAW=missing",
        expected="root or CAP_NET_RAW",
        remediation=(
            "sudo setcap cap_net_raw,cap_net_admin=eip $(readlink -f $(which python3))"
        ),
        platforms=frozenset({"linux"}),
    )


@register_check(
    "Interface exists",
    platforms={"linux"},
    category="linux",
)
def check_linux_iface_exists(*, interface: Optional[str] = None,
                             **_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    if not interface:
        return _timed_result(
            "Interface exists", t0, CheckStatus.SKIP,
            observed="no --interface specified",
            expected="interface name provided",
            platforms=frozenset({"linux"}),
        )
    path = Path("/sys/class/net") / interface
    if path.exists():
        return _timed_result(
            "Interface exists", t0, CheckStatus.PASS,
            observed=f"{interface} found in /sys/class/net",
            expected=f"{interface} exists",
            platforms=frozenset({"linux"}),
        )
    return _timed_result(
        "Interface exists", t0, CheckStatus.FAIL,
        observed=f"{path} not found",
        expected=f"{interface} exists",
        remediation=(
            f"ip link | grep -i {interface}  — if missing, check dmesg for "
            "driver load errors; confirm modem is powered + cabled"
        ),
        platforms=frozenset({"linux"}),
    )


@register_check(
    "Interface is UP",
    platforms={"linux"},
    category="linux",
)
def check_linux_iface_up(*, interface: Optional[str] = None,
                        **_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    if not interface:
        return _timed_result(
            "Interface is UP", t0, CheckStatus.SKIP,
            observed="no --interface specified",
            expected="operstate=up",
            platforms=frozenset({"linux"}),
        )
    sys_path = Path("/sys/class/net") / interface / "operstate"
    try:
        state = sys_path.read_text().strip()
    except OSError:
        return _timed_result(
            "Interface is UP", t0, CheckStatus.FAIL,
            observed=f"{sys_path} unreadable",
            expected="operstate=up",
            platforms=frozenset({"linux"}),
        )
    if state == "up":
        return _timed_result(
            "Interface is UP", t0, CheckStatus.PASS,
            observed=f"operstate={state}",
            expected="operstate=up",
            platforms=frozenset({"linux"}),
        )
    return _timed_result(
        "Interface is UP", t0, CheckStatus.FAIL,
        observed=f"operstate={state}",
        expected="operstate=up",
        remediation=f"sudo ip link set {interface} up",
        platforms=frozenset({"linux"}),
    )


@register_check(
    "Interface MTU >= 1500",
    platforms={"linux"},
    category="linux",
)
def check_linux_iface_mtu(*, interface: Optional[str] = None,
                         **_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    if not interface:
        return _timed_result(
            "Interface MTU >= 1500", t0, CheckStatus.SKIP,
            observed="no --interface specified",
            expected="MTU >= 1500",
            platforms=frozenset({"linux"}),
        )
    try:
        mtu = int((Path("/sys/class/net") / interface / "mtu")
                  .read_text().strip())
    except (OSError, ValueError) as e:
        return _timed_result(
            "Interface MTU >= 1500", t0, CheckStatus.FAIL,
            observed=f"mtu read failed: {e}",
            expected="MTU >= 1500",
            platforms=frozenset({"linux"}),
        )
    if mtu >= 1500:
        return _timed_result(
            "Interface MTU >= 1500", t0, CheckStatus.PASS,
            observed=f"MTU={mtu}",
            expected="MTU >= 1500",
            platforms=frozenset({"linux"}),
        )
    return _timed_result(
        "Interface MTU >= 1500", t0, CheckStatus.FAIL,
        observed=f"MTU={mtu}",
        expected="MTU >= 1500",
        remediation=f"sudo ip link set {interface} mtu 1500",
        platforms=frozenset({"linux"}),
    )


@register_check(
    "Interface carrier detected",
    platforms={"linux"},
    category="linux",
)
def check_linux_iface_carrier(*, interface: Optional[str] = None,
                             **_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    if not interface:
        return _timed_result(
            "Interface carrier detected", t0, CheckStatus.SKIP,
            observed="no --interface specified",
            expected="carrier=1 (modem link-up)",
            platforms=frozenset({"linux"}),
        )
    try:
        carrier = (Path("/sys/class/net") / interface / "carrier") \
                  .read_text().strip()
    except OSError as e:
        return _timed_result(
            "Interface carrier detected", t0, CheckStatus.FAIL,
            observed=f"carrier read failed: {e}",
            expected="carrier=1",
            platforms=frozenset({"linux"}),
        )
    if carrier == "1":
        return _timed_result(
            "Interface carrier detected", t0, CheckStatus.PASS,
            observed="carrier=1",
            expected="carrier=1",
            platforms=frozenset({"linux"}),
        )
    return _timed_result(
        "Interface carrier detected", t0, CheckStatus.FAIL,
        observed=f"carrier={carrier}",
        expected="carrier=1 (modem PLC-link-up)",
        remediation=(
            "Modem is not link-up. Power-cycle the modem, check the cable, "
            "and confirm the peer modem on the other end is powered."
        ),
        platforms=frozenset({"linux"}),
    )


@register_check(
    "Interface link speed",
    platforms={"linux"},
    category="linux",
)
def check_linux_iface_speed(*, interface: Optional[str] = None,
                           **_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    if not interface:
        return _timed_result(
            "Interface link speed", t0, CheckStatus.SKIP,
            observed="no --interface specified",
            expected="10 or 100 Mbps",
            platforms=frozenset({"linux"}),
        )
    try:
        speed = int((Path("/sys/class/net") / interface / "speed")
                   .read_text().strip())
    except (OSError, ValueError):
        return _timed_result(
            "Interface link speed", t0, CheckStatus.SKIP,
            observed="speed unreadable (common for PLC modems)",
            expected="10 or 100 Mbps",
            platforms=frozenset({"linux"}),
        )
    if speed in (10, 100, 1000):
        return _timed_result(
            "Interface link speed", t0, CheckStatus.PASS,
            observed=f"{speed} Mbps",
            expected="10 / 100 / 1000 Mbps",
            platforms=frozenset({"linux"}),
        )
    return _timed_result(
        "Interface link speed", t0, CheckStatus.WARN,
        observed=f"speed={speed}",
        expected="10 / 100 / 1000 Mbps (HomePlug AV typical)",
        platforms=frozenset({"linux"}),
    )


@register_check(
    "IPv6 link-local configured",
    platforms={"linux"},
    category="linux",
)
def check_linux_ipv6_linklocal(*, interface: Optional[str] = None,
                              **_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    if not interface:
        return _timed_result(
            "IPv6 link-local configured", t0, CheckStatus.SKIP,
            observed="no --interface specified",
            expected="fe80:: address assigned to interface",
            platforms=frozenset({"linux"}),
        )
    proc = Path("/proc/net/if_inet6")
    if not proc.exists():
        return _timed_result(
            "IPv6 link-local configured", t0, CheckStatus.SKIP,
            observed=f"{proc} missing",
            expected="fe80:: on interface",
            platforms=frozenset({"linux"}),
        )
    try:
        text = proc.read_text()
    except OSError as e:
        return _timed_result(
            "IPv6 link-local configured", t0, CheckStatus.FAIL,
            observed=f"read failed: {e}",
            expected="fe80:: on interface",
            platforms=frozenset({"linux"}),
        )
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        addr_hex, _idx, _plen, scope_hex, _flags, ifname = parts[:6]
        if ifname != interface:
            continue
        # Scope 0x20 = link-local.
        if int(scope_hex, 16) != 0x20:
            continue
        return _timed_result(
            "IPv6 link-local configured", t0, CheckStatus.PASS,
            observed=f"fe80:: found on {interface}",
            expected="fe80:: on interface",
            platforms=frozenset({"linux"}),
        )
    return _timed_result(
        "IPv6 link-local configured", t0, CheckStatus.FAIL,
        observed=f"no fe80:: entry for {interface} in /proc/net/if_inet6",
        expected="fe80:: on interface",
        remediation=(
            f"sudo sysctl -w net.ipv6.conf.{interface}.disable_ipv6=0 && "
            f"sudo ip link set {interface} down && "
            f"sudo ip link set {interface} up"
        ),
        platforms=frozenset({"linux"}),
    )


@register_check(
    "IPv6 multicast reachable (ff02::1)",
    platforms={"linux"},
    category="linux",
)
def check_linux_ipv6_multicast(*, interface: Optional[str] = None,
                              **_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    if not interface:
        return _timed_result(
            "IPv6 multicast reachable (ff02::1)", t0, CheckStatus.SKIP,
            observed="no --interface specified",
            expected="ping6 -c 2 ff02::1%iface sees >= 1 reply",
            platforms=frozenset({"linux"}),
        )
    try:
        out = subprocess.run(
            ["ping6", "-c", "2", "-W", "2", f"ff02::1%{interface}"],
            capture_output=True, text=True, timeout=6,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return _timed_result(
            "IPv6 multicast reachable (ff02::1)", t0, CheckStatus.SKIP,
            observed=f"ping6 invocation failed: {e}",
            expected=">= 1 reply",
            platforms=frozenset({"linux"}),
        )
    # Any reply line in stdout means ≥ 1 host responded.
    if "received" in out.stdout:
        m = re.search(r"(\d+) received", out.stdout)
        received = int(m.group(1)) if m else 0
        if received >= 1:
            return _timed_result(
                "IPv6 multicast reachable (ff02::1)", t0, CheckStatus.PASS,
                observed=f"{received}/2 replies",
                expected=">= 1 reply",
                platforms=frozenset({"linux"}),
            )
    return _timed_result(
        "IPv6 multicast reachable (ff02::1)", t0, CheckStatus.FAIL,
        observed=out.stdout.strip().splitlines()[-1] if out.stdout else "no reply",
        expected=">= 1 reply within 2s",
        remediation=(
            "Firewall may be blocking ICMPv6 echo-request on the interface. "
            f"Try: sudo iptables -I INPUT -i {interface} -p icmpv6 -j ACCEPT"
        ),
        platforms=frozenset({"linux"}),
    )


@register_check(
    "Kernel version",
    platforms={"linux"},
    category="linux",
)
def check_linux_kernel(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    rel = platform.release()
    m = re.match(r"(\d+)\.(\d+)", rel)
    if not m:
        return _timed_result(
            "Kernel version", t0, CheckStatus.SKIP,
            observed=f"unparseable release string: {rel}",
            expected=">= 5.0",
            platforms=frozenset({"linux"}),
        )
    major, minor = int(m.group(1)), int(m.group(2))
    if (major, minor) >= (5, 0):
        return _timed_result(
            "Kernel version", t0, CheckStatus.PASS,
            observed=rel,
            expected=">= 5.0",
            platforms=frozenset({"linux"}),
        )
    return _timed_result(
        "Kernel version", t0, CheckStatus.WARN,
        observed=rel,
        expected=">= 5.0 (AF_PACKET reliability)",
        platforms=frozenset({"linux"}),
    )


# =====================================================================
# WINDOWS
# =====================================================================


@register_check(
    "Npcap installed",
    platforms={"windows"},
    category="windows",
)
def check_windows_npcap(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    candidates = [
        r"C:\Windows\System32\Npcap",
        r"C:\Program Files\Npcap",
    ]
    for p in candidates:
        if Path(p).exists():
            return _timed_result(
                "Npcap installed", t0, CheckStatus.PASS,
                observed=f"detected at {p}",
                expected="Npcap 1.70+ installed",
                platforms=frozenset({"windows"}),
            )
    return _timed_result(
        "Npcap installed", t0, CheckStatus.FAIL,
        observed="Npcap directory not found",
        expected="Npcap 1.70+ installed",
        remediation="Install Npcap from https://npcap.com (check WinPcap API compat)",
        platforms=frozenset({"windows"}),
    )


@register_check(
    "Windows version >= 10",
    platforms={"windows"},
    category="windows",
)
def check_windows_version(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    rel = platform.release()
    ver = platform.version()
    try:
        major = int(rel)
    except ValueError:
        # Newer Python returns "10" / "11" as release.
        major = 0
    if major >= 10:
        return _timed_result(
            "Windows version >= 10", t0, CheckStatus.PASS,
            observed=f"release={rel} version={ver}",
            expected=">= 10 (Npcap requires Win10)",
            platforms=frozenset({"windows"}),
        )
    return _timed_result(
        "Windows version >= 10", t0, CheckStatus.WARN,
        observed=f"release={rel}",
        expected=">= 10",
        platforms=frozenset({"windows"}),
    )


@register_check(
    "Interface visible in ipconfig",
    platforms={"windows"},
    category="windows",
)
def check_windows_iface_visible(*, interface: Optional[str] = None,
                                **_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    if not interface:
        return _timed_result(
            "Interface visible in ipconfig", t0, CheckStatus.SKIP,
            observed="no --interface specified",
            expected="interface appears in ipconfig output",
            platforms=frozenset({"windows"}),
        )
    try:
        out = subprocess.run(
            ["ipconfig.exe", "/all"],
            capture_output=True, text=True, timeout=5, encoding="ansi",
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return _timed_result(
            "Interface visible in ipconfig", t0, CheckStatus.SKIP,
            observed=f"ipconfig invocation failed: {e}",
            expected="ipconfig runs successfully",
            platforms=frozenset({"windows"}),
        )
    stdout = (out.stdout or "").lower()
    if interface.lower() in stdout:
        return _timed_result(
            "Interface visible in ipconfig", t0, CheckStatus.PASS,
            observed=f"{interface} mentioned",
            expected="interface appears in ipconfig output",
            platforms=frozenset({"windows"}),
        )
    return _timed_result(
        "Interface visible in ipconfig", t0, CheckStatus.WARN,
        observed=f"{interface} not mentioned",
        expected="interface appears in ipconfig output",
        remediation="Use `ipconfig /all` to confirm the exact interface name",
        platforms=frozenset({"windows"}),
    )


@register_check(
    "pypcap importable",
    platforms={"windows"},
    category="windows",
)
def check_pypcap_import(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    try:
        import pcap  # noqa: F401
        return _timed_result(
            "pypcap importable", t0, CheckStatus.PASS,
            observed="pcap module imported",
            expected="pypcap installed with Npcap backing",
            platforms=frozenset({"windows"}),
        )
    except ImportError as e:
        return _timed_result(
            "pypcap importable", t0, CheckStatus.WARN,
            observed=f"import failed: {e}",
            expected="pypcap importable (needed for live capture)",
            remediation="pip install pypcap  (requires Npcap SDK)",
            platforms=frozenset({"windows"}),
        )


# =====================================================================
# CROSS-PLATFORM SYSTEM
# =====================================================================


@register_check("System clock sane", category="system")
def check_system_clock(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    # Cheap sanity: time.time() should be > year 2024 epoch (1704067200).
    now = time.time()
    year_2024 = 1704067200
    year_2030 = 1893456000
    if year_2024 <= now <= year_2030:
        import datetime as _dt
        dt = _dt.datetime.utcfromtimestamp(now).isoformat()
        return _timed_result(
            "System clock sane", t0, CheckStatus.PASS,
            observed=f"UTC {dt}",
            expected="between 2024 and 2030 UTC",
        )
    return _timed_result(
        "System clock sane", t0, CheckStatus.WARN,
        observed=f"unix epoch {now}",
        expected="between 2024 and 2030 UTC",
        remediation=(
            "Enable NTP/chrony:   sudo systemctl enable --now systemd-timesyncd"
            if sys.platform.startswith("linux")
            else "Enable Windows Time service: w32tm /resync"
        ),
    )


@register_check("CPU + memory adequate", category="system")
def check_system_resources(**_kw: Any) -> CheckResult:
    t0 = time.monotonic()
    try:
        import psutil
    except ImportError:
        return _timed_result(
            "CPU + memory adequate", t0, CheckStatus.SKIP,
            observed="psutil not installed",
            expected=">= 2 cores, >= 1 GiB RAM",
        )
    cores = psutil.cpu_count(logical=False) or 1
    ram_gib = psutil.virtual_memory().total / (1024 ** 3)
    observed = f"{cores} cores, {ram_gib:.1f} GiB RAM"
    if cores >= 2 and ram_gib >= 1.0:
        return _timed_result(
            "CPU + memory adequate", t0, CheckStatus.PASS,
            observed=observed, expected=">= 2 cores, >= 1 GiB RAM",
        )
    return _timed_result(
        "CPU + memory adequate", t0, CheckStatus.WARN,
        observed=observed,
        expected=">= 2 cores, >= 1 GiB RAM",
        remediation="Close other workloads; target hardware may be underspec'd",
    )


# =====================================================================
# helpers
# =====================================================================


def _linux_has_cap_net_raw() -> bool:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    mask = int(line.split()[1], 16)
                    return bool(mask & (1 << 13))
    except OSError:
        pass
    return False


def _binary_architecture(path: Path) -> str:
    """Best-effort string describing the binary's machine type.

    Reads the first few header bytes and matches well-known magic
    numbers. Returns something like ``"ELF x86_64"`` or
    ``"PE32+ x86_64"`` or ``"unknown"``.
    """
    try:
        head = path.read_bytes()[:64]
    except OSError:
        return "unreadable"
    if head[:4] == b"\x7fELF":
        bits = 64 if head[4] == 2 else 32
        machine_byte = head[18] if len(head) > 18 else 0
        machine = {
            0x03: "x86", 0x3E: "x86_64", 0x28: "arm", 0xB7: "aarch64",
        }.get(machine_byte, f"0x{machine_byte:02x}")
        return f"ELF{bits} {machine}"
    if head[:2] == b"MZ":
        # PE32 / PE32+ — find 'PE\0\0' offset
        try:
            pe_offset = struct.unpack("<I", head[60:64])[0]
            buf = path.read_bytes()
            if buf[pe_offset:pe_offset + 4] == b"PE\x00\x00":
                machine = struct.unpack("<H", buf[pe_offset + 4:pe_offset + 6])[0]
                mname = {
                    0x014C: "x86", 0x8664: "x86_64",
                    0x01C0: "arm", 0xAA64: "aarch64",
                }.get(machine, f"0x{machine:04x}")
                return f"PE {mname}"
        except (struct.error, OSError):
            pass
        return "PE unknown"
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
                    b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return "Mach-O"
    return "unknown"
