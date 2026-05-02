#!/usr/bin/env bash
# Test HotWire PEV protocol variants against EVSE (simulation loopback).
# Protocols: din, iso, both, tesla.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MATRIX_DIR="$REPO/runs/proto_matrix_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$MATRIX_DIR"

printf '%-10s %-10s %-20s %-10s %-8s\n' "PROTO" "STATES" "MSG_TYPES" "CD_COUNT" "RESULT"
printf '%-10s %-10s %-20s %-10s %-8s\n' "-----" "------" "---------" "--------" "------"

DUR=20

for PROTO in din iso both tesla; do
  RUN="$MATRIX_DIR/$PROTO"
  mkdir -p "$RUN"
  EVSE_LOG="$RUN/evse.log"
  PEV_LOG="$RUN/pev.log"

  cd "$REPO"
  python3 -u scripts/run_evse.py > "$EVSE_LOG" 2>&1 &
  EV=$!
  sleep 1.5
  python3 -u scripts/run_pev.py --protocol "$PROTO" > "$PEV_LOG" 2>&1 &
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

  printf '%-10s %-10s %-20s %-10s %-8s\n' "$PROTO" "$STATES" "$MSG_TYPES" "$CD" "$RESULT"
done

echo
echo "Logs: $MATRIX_DIR"
