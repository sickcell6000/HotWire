"""Run the entire HotWire test suite and print a one-line summary.

Exists as a single-file test runner so CI and development can invoke
the suite without pytest on PATH. Each test module's ``__main__`` block
is invoked as an independent subprocess so failures are isolated.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# Order chosen for fail-fast: cheap tests first.
TEST_SCRIPTS = [
    # Pure-Python unit tests (fast, no Qt, no sockets).
    "test_pause_override.py",
    "test_message_observer.py",
    "test_attacks.py",
    "test_session_log.py",
    "test_session_redactor.py",
    "test_session_compare.py",
    "test_pcap_export.py",
    "test_iso15118_negotiation.py",
    "test_homeplug_factory.py",
    "test_homeplug_slac_mock.py",
    "test_homeplug_slac_replay.py",
    "test_slac_attenuation.py",
    "test_sdp.py",
    "test_din_conformance.py",
    "test_random_schema_fuzz.py",
    # Checkpoint 13 — new unit tests (fast).
    "test_stage_nav_api.py",
    "test_pcap_export_module.py",
    "test_attack_launcher.py",
    "test_session_replay.py",
    # Checkpoint 14 — preflight + GUI tool panels.
    "test_preflight_checks.py",
    "test_config_save.py",
    "test_csv_export.py",
    "test_hw_runner_panel.py",
    "test_session_compare_panel.py",
    "test_session_tools_panel.py",
    "test_config_editor.py",
    "test_live_pcap_viewer.py",
    "test_preflight_wizard.py",
    # Integration tests (open sockets, spawn threads).
    "test_tcp_loopback.py",
    "test_two_process_loopback.py",
    "test_attack_integration.py",
    "test_forced_discharge_integration.py",
]


def main() -> int:
    env = os.environ.copy()
    env["HOTWIRE_CONFIG"] = str(ROOT / "config" / "hotwire.ini")
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    passed = 0
    failed: list[str] = []
    for name in TEST_SCRIPTS:
        path = TESTS / name
        if not path.exists():
            print(f"[SKIP] {name} not found")
            continue
        print(f"[RUN ] {name}", flush=True)
        result = subprocess.run(
            [sys.executable, str(path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0:
            passed += 1
            print(f"[PASS] {name}")
        else:
            failed.append(name)
            print(f"[FAIL] {name} (exit {result.returncode})")
            print(result.stdout[-500:])
            print(result.stderr[-500:])

    print()
    print(f"Summary: {passed} passed, {len(failed)} failed "
          f"out of {passed + len(failed)}")
    for name in failed:
        print(f"  - {name}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
