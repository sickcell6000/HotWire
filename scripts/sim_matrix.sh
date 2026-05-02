#!/usr/bin/env bash
# Parametric matrix of HotWire simulation-mode V2G sessions.
# For each (voltage, duration) pair, launch EVSE + PEV, collect stats.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MATRIX_DIR="$REPO/runs/matrix_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$MATRIX_DIR"

# Auto-generate per-voltage config files derived from config/hotwire.ini.
# Without this, a fresh clone (e.g. an AEC reviewer's machine) hits a
# missing-file error because the config dir was never seeded.
CFG_DIR="/tmp/hw_matrix"
mkdir -p "$CFG_DIR"
BASE_CFG="$REPO/config/hotwire.ini"
if [ ! -f "$BASE_CFG" ]; then
  echo "ERROR: base config not found at $BASE_CFG" >&2
  exit 1
fi
for V in 200 400 800; do
  CFG_FILE="$CFG_DIR/hotwire_v${V}.ini"
  if [ ! -f "$CFG_FILE" ]; then
    sed -E "s/^charge_target_voltage *=.*/charge_target_voltage = ${V}/" \
        "$BASE_CFG" > "$CFG_FILE"
  fi
done

printf '%-8s %-10s %-10s %-20s %-10s %-8s\n' "VOLT" "DUR" "STATES" "MSG_TYPES" "CD_COUNT" "RESULT"
printf '%-8s %-10s %-10s %-20s %-10s %-8s\n' "----" "---" "------" "---------" "--------" "------"

# Duration sweep: 15 / 25 / 45 s. The shortest is 15 s rather than
# 10 s because PreCharge ramping (commit 2dd4121, ``_PRECHARGE_RAMP_STEP_V``
# = 25 V/req) takes ~3-5 s to walk from 0 V to ``charge_target_voltage``
# during PreChargeReq/Res rounds. A 10 s session would routinely abort
# in WaitForCableCheckRes before the EVSE finished ramping, so the
# shortest meaningful "completes a full DIN session" test point is now
# 15 s.
for V in 200 400 800; do
  for DUR in 15 25 45; do
    CFG="$CFG_DIR/hotwire_v${V}.ini"
    RUN="$MATRIX_DIR/v${V}_d${DUR}"
    mkdir -p "$RUN"
    EVSE_LOG="$RUN/evse.log"
    PEV_LOG="$RUN/pev.log"

    cd "$REPO"
    HOTWIRE_CONFIG="$CFG" python3 -u scripts/run_evse.py > "$EVSE_LOG" 2>&1 &
    EV=$!
    sleep 1.5
    HOTWIRE_CONFIG="$CFG" python3 -u scripts/run_pev.py  > "$PEV_LOG"  2>&1 &
    PV=$!

    sleep "$DUR"
    kill "$EV" "$PV" 2>/dev/null || true
    wait 2>/dev/null || true

    STATES=$(grep -oE 'entering [0-9]+:[A-Za-z]+' "$PEV_LOG" | sort -u | wc -l)
    MSG_TYPES=$(grep -oE 'msgName": "[A-Za-z]+' "$EVSE_LOG" | sort -u | wc -l)
    CD=$(grep -cE 'msgName": "CurrentDemandReq' "$EVSE_LOG" || echo 0)

    if [[ "$STATES" -ge 13 && "$CD" -ge 5 ]]; then
      RESULT="PASS"
    else
      RESULT="FAIL"
    fi

    printf '%-8s %-10s %-10s %-20s %-10s %-8s\n' "${V}V" "${DUR}s" "$STATES" "$MSG_TYPES" "$CD" "$RESULT"
  done
done

echo
echo "Logs: $MATRIX_DIR"
