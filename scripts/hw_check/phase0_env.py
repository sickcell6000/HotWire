"""
Phase 0 — Environment sanity check.

Before we touch hardware, make sure the host has everything the
later phases assume. This runs on the Windows dev laptop too; where
a check isn't applicable the phase degrades to SKIP rather than FAIL.

What we verify:

* The repo is importable and the OpenV2G binary is present + runnable
* ``tcpdump`` or ``dumpcap`` is on PATH (for per-phase pcap capture)
* On Linux, the requested interface exists and is UP (otherwise FAIL
  fast with a clear remediation message — running later phases with a
  dead interface wastes 5 minutes and a full run dir)
* On Linux, the process has ``CAP_NET_RAW`` or is root (needed by
  pcap and raw SLAC frames); on Windows we just note the Npcap
  install state
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Import lazily so this file can be run as a script without PYTHONPATH hacks.
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))
sys.path.insert(0, str(_THIS.parent.parent.parent))

from _runner import (  # noqa: E402
    PhaseResult,
    RunContext,
    Status,
    print_banner,
    print_result,
    run_phase,
)


def phase0_env(ctx: RunContext) -> PhaseResult:
    metrics: dict[str, object] = {}
    details: list[str] = []
    soft_warnings: list[str] = []
    hard_fails: list[str] = []

    # --- 1. Repo layout + OpenV2G binary
    codec = _repo_root() / "hotwire" / "exi" / "codec" / "OpenV2G.exe"
    # On Linux the binary is named "OpenV2G" (no extension)
    if not codec.exists():
        alt = codec.with_suffix("")
        if alt.exists():
            codec = alt
    if codec.exists():
        size = codec.stat().st_size
        metrics["codec_binary"] = str(codec)
        metrics["codec_size_bytes"] = size
        # Verify it actually runs by invoking with a trivial command.
        try:
            out = subprocess.run(
                [str(codec), "EDa_1_5A5A4445464C54"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode != 0:
                hard_fails.append(
                    f"OpenV2G exited with code {out.returncode}: "
                    f"{out.stderr[:200]!r}"
                )
            elif "result" not in out.stdout:
                hard_fails.append(
                    "OpenV2G did not produce a 'result' JSON field"
                )
            else:
                details.append("OpenV2G smoke test: OK")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            hard_fails.append(f"OpenV2G invocation failed: {e}")
    else:
        hard_fails.append(
            f"OpenV2G binary not found under {codec.parent}. "
            f"Run `python vendor/build_openv2g.py` to build it."
        )

    # --- 2. pcap capture tool
    for tool in ("tcpdump", "dumpcap"):
        if shutil.which(tool):
            metrics["pcap_tool"] = tool
            details.append(f"{tool}: available on PATH")
            break
    else:
        soft_warnings.append(
            "Neither tcpdump nor dumpcap found on PATH. "
            "Later phases will run without recording pcap."
        )

    # --- 3. Interface exists + is UP (Linux) / present (Windows)
    iface = ctx.interface
    if iface:
        if sys.platform.startswith("linux"):
            ok, msg = _linux_check_iface(iface)
            metrics["interface"] = iface
            metrics["interface_state"] = msg
            if not ok:
                hard_fails.append(
                    f"Interface {iface!r} not usable: {msg}"
                )
            else:
                details.append(f"interface {iface!r}: {msg}")
        elif sys.platform == "win32":
            # Best-effort: just check ipconfig mentions it.
            try:
                out = subprocess.run(
                    ["ipconfig.exe"],
                    capture_output=True, text=True, timeout=5,
                    encoding="ansi",
                )
                if iface and iface.lower() in (out.stdout or "").lower():
                    details.append(f"interface {iface!r}: seen in ipconfig")
                else:
                    soft_warnings.append(
                        f"interface {iface!r} not visible in ipconfig output"
                    )
            except (OSError, subprocess.TimeoutExpired) as e:
                soft_warnings.append(f"ipconfig invocation failed: {e}")
        else:
            soft_warnings.append(
                f"Interface checks not implemented for {sys.platform}"
            )
    else:
        details.append(
            "no interface configured — simulation mode phases only"
        )

    # --- 4. Raw-socket capability (Linux) / Npcap (Windows)
    if sys.platform.startswith("linux"):
        euid = os.geteuid() if hasattr(os, "geteuid") else -1
        metrics["euid"] = euid
        if euid == 0:
            details.append("running as root (SLAC + pcap will work)")
        else:
            # Check CAP_NET_RAW via /proc
            caps_ok = _linux_has_cap_net_raw()
            if caps_ok:
                details.append("CAP_NET_RAW granted (non-root pcap ok)")
            else:
                soft_warnings.append(
                    "Not root and no CAP_NET_RAW. SLAC and pcap phases "
                    "will likely fail. Run with sudo or grant the capability:\n"
                    "  sudo setcap cap_net_raw,cap_net_admin=eip $(which python3)"
                )
    elif sys.platform == "win32":
        npcap = _windows_find_npcap()
        if npcap:
            metrics["npcap"] = npcap
            details.append(f"Npcap detected at {npcap}")
        else:
            soft_warnings.append(
                "Npcap not detected. Install https://npcap.com for pcap + "
                "raw-frame support on Windows."
            )

    # --- Decide final status
    if hard_fails:
        status = Status.FAIL
        summary = hard_fails[0]
    elif soft_warnings:
        status = Status.PASS
        summary = f"environment OK ({len(soft_warnings)} warning(s))"
    else:
        status = Status.PASS
        summary = "environment OK"

    full_details = []
    if hard_fails:
        full_details.append("FAILURES:")
        full_details.extend("  - " + f for f in hard_fails)
    if soft_warnings:
        full_details.append("WARNINGS:")
        full_details.extend("  - " + w for w in soft_warnings)
    if details:
        full_details.append("NOTES:")
        full_details.extend("  - " + d for d in details)

    return PhaseResult(
        name="phase0_env",
        status=status,
        summary=summary,
        details="\n".join(full_details),
        metrics=metrics,
    )


# --- Helpers ----------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _linux_check_iface(iface: str) -> tuple[bool, str]:
    """Return (ok, state_string) for a given interface on Linux."""
    sys_path = Path("/sys/class/net") / iface
    if not sys_path.exists():
        return False, "not found in /sys/class/net"
    try:
        state = (sys_path / "operstate").read_text().strip()
    except OSError as e:
        return False, f"read operstate failed: {e}"
    if state == "up":
        return True, "up"
    return False, f"operstate={state} (expected 'up')"


def _linux_has_cap_net_raw() -> bool:
    """Cheap check for CAP_NET_RAW by reading the bounding set."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    mask = int(line.split()[1], 16)
                    # CAP_NET_RAW = 13
                    return bool(mask & (1 << 13))
    except OSError:
        pass
    return False


def _windows_find_npcap() -> str | None:
    """Look for the Npcap install dir. Lightweight — no registry parse."""
    candidates = [
        r"C:\Windows\System32\Npcap",
        r"C:\Program Files\Npcap",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


# --- CLI entrypoint ---------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interface", "-i", default=None,
        help="Ethernet interface for later phases (e.g. eth0).",
    )
    args = parser.parse_args(argv)
    print_banner("Phase 0 — environment check")
    ctx = RunContext.create_standalone(interface=args.interface)
    result = run_phase(ctx, "phase0_env", phase0_env)
    print_result(result)
    print(f"\nArtifacts: {ctx.run_dir}")
    ctx.close()
    return 0 if result.status in (Status.PASS, Status.SKIP) else 1


if __name__ == "__main__":
    sys.exit(main())
