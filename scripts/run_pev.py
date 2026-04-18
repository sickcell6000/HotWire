"""Headless entry-point: run HotWire as a PEV (electric-vehicle emulator).

Usage:

    python scripts/run_pev.py            # simulation on ::1 loopback
    python scripts/run_pev.py --hw       # real PLC modem + hardware

The PEV waits for the simulated SDP to report an EVSE at ``[::1]:57122``,
connects, and drives the full DIN 70121 handshake through to SessionStop.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Make HotWire importable when running the script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.core.config import load as load_config  # noqa: E402
from hotwire.core.modes import C_PEV_MODE  # noqa: E402
from hotwire.core.worker import HotWireWorker  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="HotWire PEV emulator")
    parser.add_argument(
        "--hw",
        action="store_true",
        help="use real hardware (pcap + PLC modem). Default is loopback simulation.",
    )
    parser.add_argument(
        "--protocol",
        choices=("din", "iso", "both", "tesla"),
        default="din",
        help="Which supportedAppProtocolReq to send. 'din' is the Ioniq blob "
             "(DIN 70121 only); 'iso' / 'both' require an OpenV2G codec with "
             "`EH_` custom-params support (falls back to 'din' with a warning). "
             "'tesla' uses the Tesla Model Y blob.",
    )
    parser.add_argument(
        "--config",
        help="path to hotwire.ini (overrides HOTWIRE_CONFIG env var)",
    )
    args = parser.parse_args()

    if args.config:
        os.environ["HOTWIRE_CONFIG"] = args.config
    elif "HOTWIRE_CONFIG" not in os.environ:
        os.environ["HOTWIRE_CONFIG"] = str(
            Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"
        )

    load_config()

    is_sim = 0 if args.hw else 1

    start_ms = int(time.time() * 1000)

    def trace(s: str) -> None:
        dt = int(time.time() * 1000) - start_ms
        print(f"[{dt}ms] {s}", flush=True)

    def show_status(s: str, selection: str = "") -> None:
        if selection:
            print(f"[STATUS/{selection}] {s}", flush=True)

    print(f"Starting HotWire PEV (simulation={bool(is_sim)}). Ctrl-C to stop.")
    worker = HotWireWorker(
        callbackAddToTrace=trace,
        callbackShowStatus=show_status,
        mode=C_PEV_MODE,
        isSimulationMode=is_sim,
        preferred_protocol=args.protocol,
    )

    try:
        while True:
            time.sleep(0.03)
            worker.mainfunction()
    except KeyboardInterrupt:
        print("\n[PEV] interrupted; exiting")
        return 0


if __name__ == "__main__":
    sys.exit(main())
