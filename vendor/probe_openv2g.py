"""Probe the bundled OpenV2G.exe to discover what parameter slots each
command accepts. Run when you need to extend ``stage_schema.py`` for a
new message type — this prints every field that comes back from the
decoder so you can see which positional arg controls which protocol field.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.exi.connector import exiDecode, exiEncode


INTERESTING_KEYS = (
    "msgName",
    "ResponseCode",
    "EVSEProcessing",
    "DC_EVSEStatus.EVSEIsolationStatus",
    "DC_EVSEStatus.EVSEStatusCode",
    "DC_EVSEStatus.EVSENotification",
    "DC_EVSEStatus.NotificationMaxDelay",
    "EVSEPresentVoltage.Value",
    "EVSEPresentVoltage.Multiplier",
    "EVSEPresentCurrent.Value",
    "EVSEPresentCurrent.Multiplier",
    "EVSEMaximumVoltageLimit.Value",
    "EVSEMaximumCurrentLimit.Value",
    "EVSECurrentLimitAchieved",
    "EVSEVoltageLimitAchieved",
    "EVSEPowerLimitAchieved",
    "EVSEID",
)


def probe(cmd: str) -> dict[str, str]:
    hex_out = exiEncode(cmd)
    if not hex_out or "error" in hex_out.lower():
        return {"_error": hex_out[:100]}
    schema = "DH" if cmd.startswith("Eh") else "D" + cmd[1]
    decoded = exiDecode(hex_out, schema)
    try:
        return json.loads(decoded)
    except json.JSONDecodeError:
        return {"_raw": decoded[:200]}


def show(cmd: str, keys: tuple[str, ...] = INTERESTING_KEYS) -> None:
    params = probe(cmd)
    print(f"\n=== {cmd} ===")
    for k in keys:
        if k in params:
            print(f"  {k} = {params[k]}")
    for k, v in params.items():
        if k.startswith("_"):
            print(f"  [{k}] {v}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for c in sys.argv[1:]:
            show(c)
    else:
        # Default probe of the three Res types we care about next.
        for cmd in (
            "EDh", "EDh_0_1_1_1_0_0",         # PowerDeliveryRes
            "EDi", "EDi_0_1_1_1_0_0",         # CurrentDemandRes
            "EDi_0_1_1_1_0_0_3_400_0_50",     # CurrentDemandRes with V/I
            "EDj", "EDj_0_1_1_1_0_0_3_5",     # WeldingDetectionRes
        ):
            show(cmd)
