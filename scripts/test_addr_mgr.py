"""Smoke test for the updated address_manager.findLinkLocalIpv6Address."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini"))

from hotwire.core.config import load
load()

from hotwire.core.address_manager import addressManager

a = addressManager(isSimulationMode=0)
print("   local IPv6 addresses:", a.localIpv6Addresses)
print("   chosen:", a.localIpv6Address)
print("   local MAC:", a.localMac.hex() if hasattr(a, "localMac") and a.localMac else None)
