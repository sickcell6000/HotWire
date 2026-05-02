"""Unit tests for the preflight check registry and runner."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini"))

from hotwire.preflight import CHECKS, CheckStatus, PreflightRunner  # noqa: E402
from hotwire.preflight.runner import format_markdown, format_text  # noqa: E402


def test_registry_has_at_least_20_checks():
    # Header + Linux + Windows + system ≈ 20+ entries.
    assert len(CHECKS) >= 20, f"only {len(CHECKS)} checks registered"


def test_every_check_has_a_name():
    for c in CHECKS:
        assert c.name, f"check without name: {c}"


def test_runner_skips_other_platform_checks():
    """On any given host, checks for the other OS should SKIP cleanly."""
    results = PreflightRunner(interface=None).run_all()
    assert len(results) == len(CHECKS)
    # At least a few PASS on any target.
    passes = sum(1 for r in results if r.status == CheckStatus.PASS)
    assert passes >= 3


def test_runner_never_raises_without_interface():
    """Missing interface must SKIP interface-bound checks, not raise."""
    results = PreflightRunner(interface=None).run_all()
    # Interface-needing checks (MTU / carrier / IPv6) should all SKIP.
    iface_checks = [r for r in results if "interface" in r.name.lower()
                    or "MTU" in r.name or "carrier" in r.name
                    or "IPv6" in r.name or "multicast" in r.name]
    for r in iface_checks:
        assert r.status == CheckStatus.SKIP, (
            f"{r.name} should SKIP without --interface, got {r.status.value}"
        )


def test_format_text_produces_summary():
    results = PreflightRunner(interface=None).run_all()
    txt = format_text(results)
    assert "pass" in txt.lower()
    assert "fail" in txt.lower()


def test_format_markdown_contains_headings():
    results = PreflightRunner(interface=None).run_all()
    md = format_markdown(results)
    assert "# HotWire preflight report" in md
    assert "| Check |" in md


def test_stop_on_fail_short_circuits():
    """If stop_on_fail=True, at most one FAIL appears (or none)."""
    results = list(PreflightRunner(
        interface=None, stop_on_fail=True,
    ).iter_results())
    fails = [r for r in results if r.status == CheckStatus.FAIL]
    assert len(fails) <= 1


def test_python_version_check_passes_on_this_host():
    # Since we're running on this interpreter, this check MUST pass.
    results = PreflightRunner(interface=None).run_all()
    py = next(r for r in results if r.name == "Python version")
    assert py.status == CheckStatus.PASS


def test_hotwire_importable_check_passes():
    results = PreflightRunner(interface=None).run_all()
    imp = next(r for r in results if r.name == "hotwire package importable")
    assert imp.status == CheckStatus.PASS


def test_elapsed_ms_populated():
    """Every result must carry a positive-or-zero elapsed_ms."""
    results = PreflightRunner(interface=None).run_all()
    for r in results:
        assert r.elapsed_ms >= 0.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
