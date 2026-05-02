"""Headless entry-point: run HotWire as an EVSE (charging-station emulator).

Usage:

    python scripts/run_evse.py            # simulation on ::1 loopback
    python scripts/run_evse.py --hw       # real PLC modem + hardware

The EVSE binds a TCP server on ``[::1]:57122`` (simulation) and waits for
a PEV process to connect. Progresses through the DIN 70121 handshake and
prints every state transition.
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
from hotwire.core.modes import C_EVSE_MODE  # noqa: E402
from hotwire.core.worker import HotWireWorker  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="HotWire EVSE emulator")
    parser.add_argument(
        "--hw",
        action="store_true",
        help="use real hardware (pcap + PLC modem). Default is loopback simulation.",
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

    print(f"Starting HotWire EVSE (simulation={bool(is_sim)}). Ctrl-C to stop.")
    worker = HotWireWorker(
        callbackAddToTrace=trace,
        callbackShowStatus=show_status,
        mode=C_EVSE_MODE,
        isSimulationMode=is_sim,
    )

    try:
        while True:
            time.sleep(0.03)
            worker.mainfunction()
    except KeyboardInterrupt:
        print("\n[EVSE] interrupted; exiting")
        return 0


if __name__ == "__main__":
    sys.exit(main())
