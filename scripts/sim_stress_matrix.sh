#!/usr/bin/env bash
# Stress test — back-to-back sessions to catch any state leak / resource
# exhaustion in worker shutdown / SDP server / TCP socket. Each run is
# independent; if any fail or any run's startup gets slower, we log it.

set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MATRIX_DIR="$REPO/runs/stress_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$MATRIX_DIR"

N="${1:-5}"
DUR=8

printf '%-6s %-10s %-12s %-20s %-10s %-8s %-10s\n' "RUN" "STATES" "STARTUP_MS" "MSG_TYPES" "CD_COUNT" "RESULT" "CLEAN?"
printf '%-6s %-10s %-12s %-20s %-10s %-8s %-10s\n' "---" "------" "----------" "---------" "--------" "------" "------"

for i in $(seq 1 "$N"); do
  RUN="$MATRIX_DIR/run_$i"
  mkdir -p "$RUN"
  EVSE_LOG="$RUN/evse.log"
  PEV_LOG="$RUN/pev.log"

  START_TS=$(date +%s%3N)
  cd "$REPO"
  python3 -u scripts/run_evse.py > "$EVSE_LOG" 2>&1 &
  EV=$!
  sleep 1.5
  python3 -u scripts/run_pev.py  > "$PEV_LOG"  2>&1 &
  PV=$!

  # Wait until PEV TCP-connects to measure startup time.
  # Real log: "Checkpoint301: connecting to [::1]:57122"
  for attempt in $(seq 1 100); do
    if grep -q 'Checkpoint301' "$PEV_LOG" 2>/dev/null; then break; fi
    sleep 0.1
    if ! kill -0 "$PV" 2>/dev/null; then break; fi
  done
  CONN_TS=$(date +%s%3N)
  STARTUP_MS=$((CONN_TS - START_TS))

  sleep "$DUR"
  kill "$EV" "$PV" 2>/dev/null || true
  wait 2>/dev/null || true

  # Check that both processes died cleanly (no zombies / leftover)
  PS_AFTER=$(pgrep -f 'run_evse.py|run_pev.py' | wc -l)
  if [[ "$PS_AFTER" -eq 0 ]]; then
    CLEAN="yes"
  else
    CLEAN="LEAK"
    pkill -9 -f 'run_evse.py|run_pev.py' 2>/dev/null || true
  fi

  STATES=$(grep -oE 'entering [0-9]+:[A-Za-z]+' "$PEV_LOG" | sort -u | wc -l)
  MSG_TYPES=$(grep -oE 'msgName": "[A-Za-z]+' "$EVSE_LOG" | sort -u | wc -l)
  CD=$(grep -cE 'msgName": "CurrentDemandReq' "$EVSE_LOG" || echo 0)

  if [[ "$STATES" -ge 13 && "$CD" -ge 5 ]]; then
    RESULT="PASS"
  else
    RESULT="FAIL"
  fi

  printf '%-6s %-10s %-12s %-20s %-10s %-8s %-10s\n' "#$i" "$STATES" "$STARTUP_MS" "$MSG_TYPES" "$CD" "$RESULT" "$CLEAN"
done

echo
echo "Logs: $MATRIX_DIR"
