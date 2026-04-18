"""
HotWire common helpers — byte-array formatting, MAC utilities, value conversion.

Adapted from pyPLC's helpers.py (GPL-3.0, uhi22).
"""
from __future__ import annotations


def twoCharHex(b: int) -> str:
    """Format a single byte as a 2-char uppercase hex string."""
    return "%0.2X" % b


def showAsHex(mybytearray: bytes | bytearray, description: str = "") -> None:
    """Print a byte-array in space-separated hex form."""
    packetlength = len(mybytearray)
    strHex = ""
    for i in range(0, packetlength):
        strHex = strHex + twoCharHex(mybytearray[i]) + " "
    print(description + "(" + str(packetlength) + "bytes) = " + strHex)


def prettyHexMessage(mybytearray: bytes | bytearray, description: str = "") -> str:
    """Return a space-separated hex string representation."""
    packetlength = len(mybytearray)
    strHex = ""
    for i in range(0, packetlength):
        strHex = strHex + twoCharHex(mybytearray[i]) + " "
    return description + "(" + str(packetlength) + "bytes) = " + strHex


def compactHexMessage(mybytearray: bytes | bytearray) -> str:
    """Return a compact (no spaces) hex string."""
    packetlength = len(mybytearray)
    strHex = ""
    for i in range(0, packetlength):
        strHex = strHex + twoCharHex(mybytearray[i])
    return strHex


def prettyMac(macByteArray: bytes | bytearray) -> str:
    """Format a 6-byte MAC as colon-separated hex (xx:xx:xx:xx:xx:xx)."""
    length = len(macByteArray)
    if length != 6:
        return "invalid MAC length " + str(length) + "!"
    s = ""
    for i in range(0, length - 1):
        s = s + twoCharHex(macByteArray[i]) + ":"
    s = s + twoCharHex(macByteArray[length - 1])
    return s


def combineValueAndMultiplier(value: str | int | float, mult: str | int) -> float:
    """Compute value * 10^mult (both given as strings or numbers)."""
    x = float(value)
    m = int(mult)
    return x * 10**m


def sanitize_string_for_command(input_string: str | None, placeholder: str = "-") -> str:
    """Sanitize a string for inclusion in underscore-delimited command strings.

    Replaces underscores with spaces and returns ``placeholder`` if the input
    is empty or None. The OpenV2G CLI uses ``_`` as parameter delimiter, so
    embedded underscores must be escaped.
    """
    if not input_string or not isinstance(input_string, str) or not input_string.strip():
        return placeholder
    sanitized_string = input_string.replace("_", " ").strip()
    if not sanitized_string:
        return placeholder
    return sanitized_string


if __name__ == "__main__":
    print("Testing the helpers")
    print(str(combineValueAndMultiplier("123", "0")) + " should be 123")
    print(str(combineValueAndMultiplier("5678", "-1")) + " should be 567.8")
    print(str(combineValueAndMultiplier("-17", "1")) + " should be -170")
    print(str(combineValueAndMultiplier("4", "4")) + " should be 40000")
