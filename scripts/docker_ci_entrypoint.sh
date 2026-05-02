#!/usr/bin/env bash
#
# HotWire CI entrypoint — runs the full test matrix inside the container
# and produces HTML coverage + JUnit XML + hw_check artifacts on the
# bind-mounted volumes.
#
# Exit code reflects the worst individual outcome (first failure wins).
#
# Phases (all on by default; override with env vars for focused reruns):
#
#   HOTWIRE_CI_REGRESSION  — full pytest suite with coverage [default 1]
#   HOTWIRE_CI_HW_CHECK    — hw_check dry-run (phase0_env + phase0_hw)  [1]
#   HOTWIRE_CI_CODEC       — codec golden byte-for-byte verify          [1]
#   HOTWIRE_CI_GUI         — GUI-smoke subset (headless Qt)             [1]
#
# The script is intentionally plain bash so the compose watch-mode
# reruns are fast (no second Python startup before pytest begins).

set -u
cd /work

HOTWIRE_CI_REGRESSION="${HOTWIRE_CI_REGRESSION:-1}"
HOTWIRE_CI_HW_CHECK="${HOTWIRE_CI_HW_CHECK:-1}"
HOTWIRE_CI_CODEC="${HOTWIRE_CI_CODEC:-1}"
HOTWIRE_CI_GUI="${HOTWIRE_CI_GUI:-1}"

# Ensure bind-mount dirs exist in case the user skipped compose.
mkdir -p runs reports htmlcov

# Track individual-phase outcomes; final exit is the worst of the lot.
overall=0

banner() {
    echo
    echo "================================================================"
    echo "  $*"
    echo "================================================================"
}

step() {
    local name="$1"; shift
    local rc
    echo
    echo "---- [$name] $* ----"
    "$@"
    rc=$?
    if [ $rc -ne 0 ]; then
        echo ">>> [$name] FAILED with exit code $rc"
        overall=$rc
    else
        echo ">>> [$name] OK"
    fi
    return $rc
}

banner "HotWire Docker CI — $(date -u +%FT%TZ)"
python -c "import sys; print('Python', sys.version)"
python -c "from PyQt6.QtCore import QT_VERSION_STR; print('Qt', QT_VERSION_STR)"
echo "QT_QPA_PLATFORM=${QT_QPA_PLATFORM}"
echo "OpenV2G binary: $(ls -la hotwire/exi/codec/OpenV2G 2>/dev/null || echo 'missing!')"

# ---- 1. Codec golden vs shipped binary ------------------------------
if [ "$HOTWIRE_CI_CODEC" = "1" ]; then
    banner "Phase 1/4 — Codec golden fixture verification"
    step codec python - <<'PYCHECK'
import json, sys, subprocess
from pathlib import Path
root = Path("/work")
golden_path = root / "tests" / "_golden_openv2g.json"
if not golden_path.exists():
    print(f"[skip] golden file not present at {golden_path}")
    sys.exit(0)
golden = json.loads(golden_path.read_text())
codec = root / "hotwire" / "exi" / "codec" / "OpenV2G"
ok = fail = 0
for cmd, case in golden.items():
    # Key is the command string; value["result"] is the expected hex blob.
    expected = case.get("result") if isinstance(case, dict) else None
    if not expected:
        continue
    try:
        out = subprocess.run(
            [str(codec), cmd], capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception as e:                                      # noqa: BLE001
        print(f"  FAIL {cmd}: codec crashed: {e}"); fail += 1; continue
    if expected in out:
        ok += 1
    else:
        print(f"  FAIL {cmd}: expected {expected[:40]!r}... not in output")
        fail += 1
print(f"\nGolden match: {ok}/{ok + fail}")
sys.exit(0 if fail == 0 else 1)
PYCHECK
fi

# ---- 2. hw_check dry-run -------------------------------------------
if [ "$HOTWIRE_CI_HW_CHECK" = "1" ]; then
    banner "Phase 2/4 — hw_check orchestrator (dry-run, no interface)"
    step hw_check python scripts/hw_check/run_all.py
fi

# ---- 3. GUI-smoke subset --------------------------------------------
# These are subset of the regression but we run them first so a GUI
# import failure (missing libxcb-* etc.) surfaces cheaply.
if [ "$HOTWIRE_CI_GUI" = "1" ]; then
    banner "Phase 3/4 — GUI headless smoke (PyQt6 offscreen)"
    step gui_smoke python -m pytest -v --tb=short \
        --junitxml=reports/gui-junit.xml \
        tests/test_gui_smoke.py \
        tests/test_gui_integration.py \
        tests/test_attack_launcher.py \
        tests/test_session_replay.py \
        tests/test_interface_picker.py \
        tests/test_interface_status_dock.py \
        tests/test_stage_nav_api.py
fi

# ---- 4. Full regression + coverage ----------------------------------
if [ "$HOTWIRE_CI_REGRESSION" = "1" ]; then
    banner "Phase 4/4 — Full regression suite with coverage"
    # Exclude conftest-free loopback tests that spawn subprocesses which
    # our container can run, but keep them in the full run. pytest-cov
    # wraps everything.
    step regression python -m pytest -v --tb=short \
        --cov=hotwire \
        --cov-report=term-missing \
        --cov-report=html:htmlcov \
        --cov-report=xml:reports/coverage.xml \
        --junitxml=reports/regression-junit.xml \
        tests/
fi

banner "Summary"
echo "Overall exit code: $overall"
echo "Artifacts:"
echo "  - Coverage HTML : ./htmlcov/index.html"
echo "  - Coverage XML  : ./reports/coverage.xml"
echo "  - JUnit regression : ./reports/regression-junit.xml"
echo "  - JUnit GUI    : ./reports/gui-junit.xml"
echo "  - hw_check runs : ./runs/"

exit "$overall"
