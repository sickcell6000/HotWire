"""
EXI connector — bridge to the bundled OpenV2G binary.

Supports DIN 70121 Request+Response (A-L / a-l) and ISO 15118-2
Request+Response via the same CLI. The binary path is resolved relative to
this module so it works whether HotWire is run from any CWD.

Adapted from pyPLC's exiConnector.py (GPL-3.0, uhi22).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..helpers import twoCharHex

# Path to the bundled OpenV2G executable.
_CODEC_DIR = Path(__file__).resolve().parent / "codec"
if os.name == "nt":
    pathToOpenV2GExe = str(_CODEC_DIR / "OpenV2G.exe")
else:
    pathToOpenV2GExe = str(_CODEC_DIR / "OpenV2G")


def exiprint(s: str) -> None:
    # Placeholder — kept for API compatibility. Use Python logging in production.
    pass


def exiHexToByteArray(hexString: str) -> bytearray:
    """Decode a hex string into a byte array; returns empty on malformed input."""
    hexlen = len(hexString)
    if (hexlen % 2) != 0:
        print("exiHexToByteArray: unplausible length of " + hexString)
        return bytearray(0)
    exiframe = bytearray(int(hexlen / 2))
    for i in range(0, int(hexlen / 2)):
        x = hexString[2 * i : 2 * i + 2]
        try:
            exiframe[i] = int(x, 16)
        except ValueError:
            print("exiHexToByteArray: unplausible data " + x)
            return bytearray(0)
    return exiframe


def exiByteArrayToHex(b: bytes | bytearray) -> str:
    s = ""
    for i in range(0, len(b)):
        s = s + twoCharHex(b[i])
    return s


def addV2GTPHeader(exidata: bytes | bytearray | str) -> bytearray:
    """Prepend the 8-byte V2GTP (Vehicle-to-Grid Transport Protocol) header."""
    if isinstance(exidata, str):
        exidata = exiHexToByteArray(exidata)
    exiLen = len(exidata)
    header = bytearray(8)
    header[0] = 0x01                       # version
    header[1] = 0xFE                       # version inverted
    header[2] = 0x80                       # payload type 0x8001 = EXI
    header[3] = 0x01
    header[4] = (exiLen >> 24) & 0xFF
    header[5] = (exiLen >> 16) & 0xFF
    header[6] = (exiLen >> 8) & 0xFF
    header[7] = exiLen & 0xFF
    return header + exidata


def removeV2GTPHeader(v2gtpData: bytes | bytearray) -> bytes | bytearray:
    return v2gtpData[8:]


def exiDecode(exiHex: str | bytes | bytearray, prefix: str = "DH") -> str:
    """Run the OpenV2G binary in decode mode. Returns JSON text from stdout.

    Prefix options:
      - ``DH``/``Dh`` — supported app protocol handshake
      - ``DD``        — DIN 70121 message
      - ``D1``        — ISO 15118-2 message
      - ``D2``        — ISO 15118-20 message
    """
    if isinstance(exiHex, (bytearray, bytes)):
        exiHex = exiByteArrayToHex(exiHex)
    param1 = prefix + exiHex
    result = subprocess.run(
        [pathToOpenV2GExe, param1], capture_output=True, text=True
    )
    if len(result.stderr) > 0:
        print("exiDecode ERROR. stderr:" + result.stderr)
    return result.stdout


def exiEncode(strMessageName: str) -> str:
    """Run the OpenV2G binary in encode mode. Returns hex-encoded EXI payload.

    The parameter string encodes both message selection and field values.
    Schemas::

        Eh       = supportedAppProtocolResponse
        EDA_...  = DIN SessionSetupRequest (with EVCCID)
        EDa_...  = DIN SessionSetupResponse (with EVSEID)
        EDG_...  = DIN PreChargeRequest (with EVTargetVoltage — A2 attack control)
        EDg_...  = DIN PreChargeResponse (with EVSEPresentVoltage — A2 attack control)
        E1A_...  = ISO 15118-2 SessionSetupRequest
        ...
    """
    exiprint("[EXICONNECTOR] exiEncode " + strMessageName)
    result = subprocess.run(
        [pathToOpenV2GExe, strMessageName], capture_output=True, text=True
    )
    if len(result.stderr) > 0:
        strConverterResult = "exiEncode ERROR. stderr:" + result.stderr
        print(strConverterResult)
        return strConverterResult
    try:
        jsondict = json.loads(result.stdout)
        strConverterResult = jsondict.get("result", "")
        strConverterError = jsondict.get("error", "")
        if len(strConverterError) > 0:
            print("[EXICONNECTOR] exiEncode error " + strConverterError)
        return strConverterResult
    except json.JSONDecodeError:
        print("exiEncode: failed to parse JSON from OpenV2G output")
        return ""


if __name__ == "__main__":
    print(f"Testing EXI connector with codec at {pathToOpenV2GExe}")
    print("--- DIN SessionSetupReq (A1-P2 impersonation) ---")
    print(exiEncode("EDA_d83add22f182"))
    print("--- DIN PreChargeReq (victim EV voltage claim) ---")
    print(exiEncode("EDG_DEAD55AADEAD55AA_80_350"))
    print("--- Decode DIN SessionSetupReq ---")
    print(exiDecode("809a02000000000000000011d01b60eb748bc60800", "DD"))
