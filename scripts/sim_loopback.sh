#!/usr/bin/env bash
# Run a full HotWire EVSE + PEV simulation-mode V2G session on the
# current host. Both processes talk over ::1 IPv6 loopback; no real
# PLC modem needed.
#
# Usage:
#   ./scripts/sim_loopback.sh             # 25 s session (default)
#   ./scripts/sim_loopback.sh 60          # custom duration in seconds
#
# Writes two logs under runs/sim_<timestamp>/ and prints a short
# summary (state transitions + message counts) at the end.

set -euo pipefail

DURATION="${1:-25}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$REPO_DIR/runs/sim_$STAMP"
mkdir -p "$RUN_DIR"

EVSE_LOG="$RUN_DIR/evse.log"
PEV_LOG="$RUN_DIR/pev.log"

echo "HotWire simulation loopback"
echo "  repo     : $REPO_DIR"
echo "  duration : ${DURATION}s"
echo "  logs     : $RUN_DIR"
echo

cd "$REPO_DIR"

# Pick a free ephemeral IPv6 TCP port and pin it via the
# HOTWIRE_TCP_PORT_OVERRIDE env var (honoured by
# hotwire/plc/tcp_socket.py::_resolve_tcp_port). The default 57122
# from config/hotwire.ini gets held in TIME_WAIT for ~2 minutes
# after every run on Linux, and any leftover process from a prior
# failed verification (e.g. AEC reviewers iterating) holds it for
# the whole TCP server lifetime. Using a fresh port per invocation
# makes the script robust to both conditions.
if [ -z "${HOTWIRE_TCP_PORT_OVERRIDE:-}" ]; then
    HOTWIRE_TCP_PORT_OVERRIDE=$(python3 -c '
import socket
s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
s.bind(("::1", 0))
print(s.getsockname()[1])
s.close()
' 2>/dev/null) || HOTWIRE_TCP_PORT_OVERRIDE=""
    if [ -n "$HOTWIRE_TCP_PORT_OVERRIDE" ]; then
        export HOTWIRE_TCP_PORT_OVERRIDE
        echo "  port     : ${HOTWIRE_TCP_PORT_OVERRIDE} (dynamic, IPv6 ::1)"
        echo
    fi
fi

# Start EVSE first; give it a second to bind the TCP server before
# PEV tries to connect.
python3 -u scripts/run_evse.py > "$EVSE_LOG" 2>&1 &
EVSE_PID=$!
sleep 1.5

python3 -u scripts/run_pev.py  > "$PEV_LOG"  2>&1 &
PEV_PID=$!

# Ensure both processes are cleaned up even if the script is
# interrupted by Ctrl-C or errors.
trap 'kill "$EVSE_PID" "$PEV_PID" 2>/dev/null || true; wait 2>/dev/null || true' EXIT

echo "EVSE pid=$EVSE_PID, PEV pid=$PEV_PID — running for ${DURATION}s..."
sleep "$DURATION"

echo
echo "Stopping both processes..."
kill "$EVSE_PID" "$PEV_PID" 2>/dev/null || true
wait 2>/dev/null || true

echo
echo "=== PEV state transitions ==="
grep -oE 'entering [0-9]+:[A-Za-z]+' "$PEV_LOG" | sort -u

echo
echo "=== EVSE V2G message types handled ==="
grep -oE 'msgName": "[A-Za-z]+' "$EVSE_LOG" | sort | uniq -c | sort -rn

echo
echo "=== PEV final status ==="
grep -E 'STATUS/pevState|STATUS/EVSEPresent' "$PEV_LOG" | tail -5

echo
echo "Logs preserved at: $RUN_DIR"
