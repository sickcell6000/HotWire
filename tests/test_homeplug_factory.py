"""Tests for B2 — HomePlug driver factory.

The factory must return a :class:`SimulatedHomePlug` in simulation mode
and try :class:`RealHomePlug` otherwise, falling back to simulation if
pcap isn't available.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini")
)


def _stub_trace_status():
    traces: list[str] = []
    statuses: list[tuple[str, str]] = []
    return traces, statuses, traces.append, (lambda s, k="": statuses.append((s, k)))


def test_factory_returns_simulated_in_sim_mode():
    from hotwire.core.modes import C_EVSE_MODE
    from hotwire.plc.homeplug import build_homeplug
    from hotwire.plc.simulation import SimulatedHomePlug

    traces, statuses, tr, st = _stub_trace_status()

    class _AM: pass
    class _CM:
        def ModemFinderOk(self, n): pass
        def SlacOk(self): pass
        def SdpOk(self): pass
        def TcpOk(self): pass
        def ApplOk(self, *a, **kw): pass
        def mainfunction(self): pass
        def getConnectionLevel(self): return 0

    hp = build_homeplug(tr, st, C_EVSE_MODE, _AM(), _CM(), isSimulationMode=1)
    assert isinstance(hp, SimulatedHomePlug)


def test_factory_falls_back_when_pcap_missing():
    """In simulation mode, or when pcap is missing, the factory must
    still return a working simulation driver — never None or raise."""
    from hotwire.core.modes import C_PEV_MODE
    from hotwire.plc.homeplug import build_homeplug
    from hotwire.plc.simulation import SimulatedHomePlug

    traces: list[str] = []

    class _AM:
        def setSeccIp(self, ip): pass
        def setSeccTcpPort(self, p): pass

    class _CM:
        def ModemFinderOk(self, n): pass
        def SlacOk(self): pass
        def SdpOk(self): pass

    # isSimulationMode=0 means "try real driver first"; if pypcap or the
    # interface is missing on this CI box the factory falls back to the
    # simulated driver. Either outcome is acceptable — both are classes the
    # worker knows how to drive.
    hp = build_homeplug(
        traces.append,
        lambda s, k="": None,
        C_PEV_MODE,
        _AM(),
        _CM(),
        isSimulationMode=0,
    )
    assert hp is not None
    # Has the API the worker expects.
    for method in ("mainfunction", "enterPevMode", "enterEvseMode",
                   "enterListenMode", "sendTestFrame", "printToUdp",
                   "sendSpecialMessageToControlThePowerSupply"):
        assert hasattr(hp, method), f"missing method {method}"


def test_real_driver_rejects_simulation_mode():
    """RealHomePlug(isSimulationMode=1) should raise — simulation mode is
    the other driver's responsibility."""
    import pytest
    from hotwire.core.modes import C_EVSE_MODE
    from hotwire.plc.homeplug import RealHomePlug

    with pytest.raises(RuntimeError):
        RealHomePlug(
            lambda s: None,
            lambda s, k="": None,
            C_EVSE_MODE,
            None,
            None,
            isSimulationMode=1,
        )
