"""Unit tests for hotwire.net.interfaces — the psutil-backed NIC enumerator.

We mock psutil so tests are deterministic across Windows / Linux / CI.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hotwire.net.interfaces import (                             # noqa: E402
    NetInterface, _score, list_interfaces,
)


def _mk_addr(family, address, mask=None):
    return SimpleNamespace(family=family, address=address, netmask=mask)


def _install_psutil_mocks(monkeypatch, addrs: dict, stats: dict,
                          af_link_code: int = -1):
    import psutil
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: addrs)
    monkeypatch.setattr(psutil, "net_if_stats", lambda: stats)
    monkeypatch.setattr(psutil, "AF_LINK", af_link_code, raising=False)


# --- scoring rubric tests ------------------------------------------------


def test_loopback_is_heavily_penalised():
    data = {
        "name": "lo", "display_name": "Loopback Pseudo-Interface",
        "mac": "00:00:00:00:00:00", "mtu": 65536,
        "is_up": True, "has_carrier": None, "speed_mbps": None,
        "ipv4": ["127.0.0.1"], "ipv6": ["::1"],
    }
    score, reasons = _score(data)
    assert score < -50, f"loopback should be very negative, got {score}"


def test_qca_oui_boosts_score():
    base = {
        "name": "eth1", "display_name": "",
        "mac": "aa:bb:cc:dd:ee:ff", "mtu": 1500,
        "is_up": True, "has_carrier": True, "speed_mbps": 100,
        "ipv4": [], "ipv6": ["fe80::1"],
    }
    qca = {**base, "mac": "00:b0:52:12:34:56"}
    s_generic, _ = _score(base)
    s_qca, reasons = _score(qca)
    assert s_qca > s_generic, "QCA OUI should outrank generic MAC"
    assert any("QCA" in r or "Qualcomm" in r for r in reasons)


def test_up_with_carrier_beats_down():
    up_with_carrier = {
        "name": "eth0", "display_name": "",
        "mac": "aa:bb:cc:dd:ee:ff", "mtu": 1500,
        "is_up": True, "has_carrier": True, "speed_mbps": 100,
        "ipv4": [], "ipv6": ["fe80::1"],
    }
    down = {**up_with_carrier, "is_up": False, "has_carrier": None}
    s_up, _ = _score(up_with_carrier)
    s_down, _ = _score(down)
    assert s_up > s_down


def test_private_ipv4_penalised():
    with_private = {
        "name": "eth0", "display_name": "",
        "mac": "aa:bb:cc:dd:ee:ff", "mtu": 1500,
        "is_up": True, "has_carrier": True, "speed_mbps": 100,
        "ipv4": ["192.168.1.50"], "ipv6": [],
    }
    without = {**with_private, "ipv4": []}
    assert _score(with_private)[0] < _score(without)[0]


# --- list_interfaces() integration tests --------------------------------


def test_list_interfaces_returns_sorted_best_first(monkeypatch):
    addrs = {
        "eth_good": [
            _mk_addr(-1, "00:b0:52:aa:bb:cc"),        # QCA OUI
            _mk_addr(socket.AF_INET6, "fe80::1"),
        ],
        "lo": [
            _mk_addr(-1, "00:00:00:00:00:00"),
            _mk_addr(socket.AF_INET, "127.0.0.1"),
            _mk_addr(socket.AF_INET6, "::1"),
        ],
        "vEthernet (virtual)": [
            _mk_addr(-1, "aa:aa:aa:aa:aa:aa"),
            _mk_addr(socket.AF_INET, "192.168.1.1"),
        ],
    }
    stats = {
        "eth_good": SimpleNamespace(isup=True, mtu=1500, speed=100),
        "lo": SimpleNamespace(isup=True, mtu=65536, speed=0),
        "vEthernet (virtual)": SimpleNamespace(isup=True, mtu=1500, speed=1000),
    }
    _install_psutil_mocks(monkeypatch, addrs, stats)

    result = list_interfaces()
    assert len(result) == 3
    # Highest-scored first.
    assert result[0].name == "eth_good"
    # Virtual + loopback should trail.
    tail_names = {r.name for r in result[1:]}
    assert "lo" in tail_names
    assert "vEthernet (virtual)" in tail_names


def test_list_interfaces_handles_empty_psutil(monkeypatch):
    _install_psutil_mocks(monkeypatch, {}, {})
    assert list_interfaces() == []


def test_list_interfaces_tolerates_missing_stats(monkeypatch):
    addrs = {"eth1": [_mk_addr(-1, "aa:bb:cc:dd:ee:ff")]}
    _install_psutil_mocks(monkeypatch, addrs, {})      # no stats entry
    result = list_interfaces()
    assert len(result) == 1
    assert result[0].name == "eth1"
    assert result[0].is_up is False


def test_net_interface_tooltip_renders():
    ni = NetInterface(
        name="eth0", display_name="Ethernet 1",
        mac="00:b0:52:aa:bb:cc", mtu=1500,
        is_up=True, has_carrier=True, speed_mbps=100,
        ipv4=[], ipv6=["fe80::1"],
        score=33, reasons=["+10 up", "+20 QCA OUI"],
    )
    tip = ni.tooltip()
    assert "eth0" in tip
    assert "1500" in tip
    assert "33" in tip
    assert "QCA OUI" in tip


def test_net_interface_short_label_has_score_hint():
    ni = NetInterface(
        name="eth0", display_name="",
        mac="aa:bb:cc:dd:ee:ff", mtu=1500,
        is_up=True, has_carrier=True, speed_mbps=100,
        ipv4=[], ipv6=[], score=33, reasons=[],
    )
    label = ni.short_label()
    assert "eth0" in label
    assert "score=33" in label
    assert "best" in label or "good" in label


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
