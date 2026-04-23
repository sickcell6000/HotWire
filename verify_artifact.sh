#!/usr/bin/env bash
# HotWire — Artifact Functional-badge verification script.
#
# Runs four checks that together confirm the core functionality
# claimed in the paper:
#
#   F1  Docker CI regression (240 tests)
#   F2  Simulation-mode full DIN 70121 session (13 states, ::1 loopback)
#   F3  Parametric matrix (9 voltage × duration runs)
#   F4  Attack-code presence (A1 + A2 syntactically valid)
#
# Total runtime: ~5 minutes with the image pre-loaded from
# hotwire-ci.tar.gz, ~25 minutes if building from source.
#
# Expected final line: "[verify_artifact] ✓ ALL FUNCTIONAL CHECKS PASSED".
#
# This script is designed for the USENIX WOOT '26 Artifact Evaluation
# Committee; see ARTIFACT.md for the Available + Functional badge
# context and known limitations.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

BOLD="$(printf '\033[1m')"
RED="$(printf '\033[31m')"
GREEN="$(printf '\033[32m')"
YELLOW="$(printf '\033[33m')"
RESET="$(printf '\033[0m')"

pass_count=0
fail_count=0
warn_count=0

log()  { printf '%s[verify_artifact]%s %s\n' "$BOLD" "$RESET" "$*"; }
ok()   { printf '%s[verify_artifact] ✓%s %s\n' "$GREEN" "$RESET" "$*"; pass_count=$((pass_count+1)); }
warn() { printf '%s[verify_artifact] ⚠%s %s\n' "$YELLOW" "$RESET" "$*"; warn_count=$((warn_count+1)); }
fail() { printf '%s[verify_artifact] ✗%s %s\n' "$RED" "$RESET" "$*"; fail_count=$((fail_count+1)); }

banner() {
    echo
    printf '%s================================================================%s\n' "$BOLD" "$RESET"
    printf '%s  %s%s\n' "$BOLD" "$*" "$RESET"
    printf '%s================================================================%s\n' "$BOLD" "$RESET"
    echo
}

banner "HotWire artifact verification — WOOT '26 AEC"
log "Repository root: $REPO_DIR"
log "Starting: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Expected runtime: ~5 min (pre-loaded image) to ~25 min (build from source)"

# --------------------------------------------------------------
# F1 — Docker CI regression
# --------------------------------------------------------------
banner "F1 — Docker CI regression (240 unit + integration tests)"

if ! command -v docker >/dev/null 2>&1; then
    fail "docker command not found — install Docker Desktop (Windows/macOS) or docker-ce (Linux), then re-run"
else
    if ! docker info >/dev/null 2>&1; then
        fail "docker daemon unreachable — on Windows, open Docker Desktop and wait for 'Engine running'; then re-run"
    else
        log "Docker daemon reachable; building (if needed) and running CI..."
        if docker compose run --rm hotwire-ci > /tmp/hotwire_ci_f1.log 2>&1; then
            if grep -qE '^[0-9]+ passed' /tmp/hotwire_ci_f1.log; then
                summary=$(grep -E '^[0-9]+ passed' /tmp/hotwire_ci_f1.log | tail -1)
                ok "F1 Docker CI regression: $summary"
            else
                fail "F1 ran but no pytest summary line found in /tmp/hotwire_ci_f1.log"
            fi
        else
            fail "F1 Docker CI failed; full log at /tmp/hotwire_ci_f1.log"
            tail -20 /tmp/hotwire_ci_f1.log
        fi
    fi
fi

# --------------------------------------------------------------
# F2 — Simulation-mode full DIN 70121 session
# --------------------------------------------------------------
banner "F2 — Simulation-mode full V2G session (::1 loopback, 25 s)"

if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not found on PATH — install Python 3.9+"
else
    if [ ! -x scripts/sim_loopback.sh ]; then
        chmod +x scripts/sim_loopback.sh 2>/dev/null || true
    fi
    log "Running scripts/sim_loopback.sh 25 (~30 s end-to-end)..."
    if bash scripts/sim_loopback.sh 25 > /tmp/hotwire_f2.log 2>&1; then
        states=$(grep -oE 'entering [0-9]+:[A-Za-z]+' /tmp/hotwire_f2.log | sort -u | wc -l)
        cd_count=$(grep -cE 'CurrentDemandReq' /tmp/hotwire_f2.log || echo 0)
        if [ "$states" -ge 13 ] && [ "$cd_count" -ge 5 ]; then
            ok "F2 sim V2G session: $states PEV states reached, $cd_count CurrentDemandReq cycles"
        else
            fail "F2 sim V2G session under-ran: only $states states, $cd_count CD cycles (expected ≥13 states, ≥5 CD)"
            tail -30 /tmp/hotwire_f2.log
        fi
    else
        fail "F2 sim_loopback.sh exited non-zero; full log at /tmp/hotwire_f2.log"
        tail -20 /tmp/hotwire_f2.log
    fi
fi

# --------------------------------------------------------------
# F3 — Parametric matrix (9 runs)
# --------------------------------------------------------------
banner "F3 — Parametric matrix (voltage × duration, 9 runs, ~3 min)"

if [ ! -x scripts/sim_matrix.sh ]; then
    chmod +x scripts/sim_matrix.sh 2>/dev/null || true
fi

log "Running scripts/sim_matrix.sh..."
if bash scripts/sim_matrix.sh > /tmp/hotwire_f3.log 2>&1; then
    pass_rows=$(grep -cE 'PASS' /tmp/hotwire_f3.log || echo 0)
    fail_rows=$(grep -cE 'FAIL' /tmp/hotwire_f3.log || echo 0)
    if [ "$pass_rows" -ge 9 ] && [ "$fail_rows" -eq 0 ]; then
        ok "F3 parametric matrix: 9/9 PASS"
    else
        fail "F3 parametric matrix failed: $pass_rows PASS, $fail_rows FAIL rows"
        tail -20 /tmp/hotwire_f3.log
    fi
else
    fail "F3 sim_matrix.sh exited non-zero; log at /tmp/hotwire_f3.log"
    tail -20 /tmp/hotwire_f3.log
fi

# --------------------------------------------------------------
# F4 — Attack code presence
# --------------------------------------------------------------
banner "F4 — Attack code is present and syntactically valid"

for f in hotwire/attacks/autocharge_impersonation.py hotwire/attacks/forced_discharge.py; do
    if [ ! -f "$f" ]; then
        fail "F4 missing: $f"
    elif python3 -m py_compile "$f" 2>/tmp/hotwire_f4.log; then
        size=$(wc -l < "$f")
        ok "F4 present + compiles: $f ($size lines)"
    else
        fail "F4 syntax error in $f:"
        cat /tmp/hotwire_f4.log
    fi
done

# --------------------------------------------------------------
# Summary
# --------------------------------------------------------------
banner "Summary"

total=$((pass_count + fail_count))
log "Checks passed : $pass_count / $total"
log "Checks failed : $fail_count"
log "Warnings      : $warn_count"
log "Finished      : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Full logs     : /tmp/hotwire_ci_f1.log, /tmp/hotwire_f2.log, /tmp/hotwire_f3.log"

echo
if [ "$fail_count" -eq 0 ]; then
    printf '%s[verify_artifact] ✓ ALL FUNCTIONAL CHECKS PASSED%s\n' "$GREEN" "$RESET"
    exit 0
else
    printf '%s[verify_artifact] ✗ %d CHECK(S) FAILED — see logs above or contact the AEC chair%s\n' \
        "$RED" "$fail_count" "$RESET"
    exit 1
fi
