# Parametric Test Matrix — 2026-04-23

All HotWire phases and simulation-mode V2G sessions, exercised under varying
parameters to verify the state machines behave correctly across edge cases
(budget timeouts, IPv6 scope, voltage targets, protocol variants, repeated
sessions). Executed on Pi (`192.168.1.44`), no peer modem required.

## Aggregate result

**32 runs total: 24 PASS + 8 expected-FAIL (no peer modem)**

All expected-FAIL outcomes were graceful budget-timeouts, not crashes or
unhandled exceptions.

---

## 1. phase1_link — passive HPAV sniff × 3 durations

All PASS; verifies pcap recording path works across short and long captures.

| Duration | Frames captured | Result |
|---|---|---|
| 2 s | 0 | PASS |
| 5 s | 0 | PASS |
| 10 s | 0 | PASS |

0 frames is expected (no peer on the powerline). The PASS requires only
that the capture tool started, wrote a pcap artifact, and stopped cleanly.

---

## 2. phase2_slac — state machine × 2 roles × 2 budgets

All 4 combinations fail-gracefully at budget expiry (no peer responding).
The number of retries scales with budget, confirming the 2 s retry cadence
inside `SlacStateMachine.tick()`.

| Role | Budget | trace_lines | final_state | Result |
|---|---|---|---|---|
| pev | 3 s | 3 | SLAC_FAILED (9) | FAIL-graceful |
| pev | 8 s | 5 | SLAC_FAILED (9) | FAIL-graceful |
| evse | 3 s | 1 | SLAC_FAILED (9) | FAIL-graceful |
| evse | 8 s | 1 | SLAC_FAILED (9) | FAIL-graceful |

### Observations

* **PEV retry cadence correct**: 3 s budget → 3 trace lines (one kick-off
  + one retry); 8 s budget → 5 trace lines (kick-off + 4 retries at 2 s
  each). Matches the Checkpoint 19 retry logic.
* **EVSE is passive** as expected: 1 trace line (just "waiting for
  PARAM.REQ") regardless of budget — EVSE doesn't kick off anything.
* **No crashes across any combination**. Every run wrote a clean
  `session.jsonl` with `phase.end status=FAIL`.

---

## 3. phase3_sdp — SDP discovery × 2 roles × 2 scopes

All 4 combinations fail-gracefully. Both SDP client and server bind
correctly on scope 0 (OS default) and scope 2 (explicit eth0 index).

| Role | Scope | observed_sdp_requests | secc_ip auto-detected | Result |
|---|---|---|---|---|
| pev | 0 | — (no response) | n/a | FAIL-graceful |
| pev | 2 | — (no response) | n/a | FAIL-graceful |
| evse | 0 | 0 | `fe80::767d:3f18:5b28:8e37` | FAIL-graceful |
| evse | 2 | 0 | `fe80::767d:3f18:5b28:8e37` | FAIL-graceful |

### Observations

* **SdpServer correctly auto-detects link-local SECC address** in both
  scope configurations, matching the Checkpoint 19 fix in
  `hotwire/core/worker.py::_start_sdp_server_if_needed`.
* **No `WinError 10049`-style bind failure** on either scope.
* Budget timeout messages are operator-readable and point at the right
  next action ("Confirm PEV multicasting..." vs "SLAC complete...").

---

## 4. V2G simulation — voltage × duration (3 × 3 = 9 runs)

Full DIN 70121 session against `::1` loopback. Every combination reached
the CurrentDemand charging loop.

| Voltage | Duration | PEV states | EVSE msg types | CD cycles | Result |
|---|---|---|---|---|---|
| 200 V | 10 s | 13 | 10 | 158 | **PASS** |
| 200 V | 25 s | 13 | 10 | 568 | **PASS** |
| 200 V | 45 s | 13 | 10 | 1127 | **PASS** |
| 400 V | 10 s | 13 | 10 | 161 | **PASS** |
| 400 V | 25 s | 13 | 10 | 560 | **PASS** |
| 400 V | 45 s | 13 | 10 | 1127 | **PASS** |
| 800 V | 10 s | 13 | 10 | 162 | **PASS** |
| 800 V | 25 s | 13 | 10 | 572 | **PASS** |
| 800 V | 45 s | 13 | 10 | 1129 | **PASS** |

### Observations

* **CurrentDemand rate stable at ~22-25 Hz** (CD count / duration).
  Production EV simulator target is 3 Hz minimum; we're 7× headroom.
* **CD count scales linearly with duration** — no stall, no memory leak
  causing slowdown mid-session.
* **Voltage target has no effect on throughput**, as expected: that
  parameter only populates `EVSEMaximumVoltageLimit` in
  `ChargeParameterDiscoveryRes`, it doesn't gate the state machine.
* All 13 PEV states reached (`2:Connected` through `14:CurrentDemand`).
* All 10 EVSE V2G message types processed (supportedAppProtocol,
  SessionSetup, ServiceDiscovery, ServicePayment, ContractAuth,
  ChargeParameter, CableCheck, PreCharge, PowerDelivery, CurrentDemand).

---

## 5. Protocol variants × 4

PEV-side `--protocol` flag drives which supportedAppProtocolReq blob is
sent. All four variants complete the handshake and reach CurrentDemand.

| --protocol | PEV states | EVSE msg types | CD cycles (20 s) | Result |
|---|---|---|---|---|
| din | 13 | 10 | 434 | **PASS** |
| iso | 13 | 10 | 435 | **PASS** |
| both | 13 | 10 | 438 | **PASS** |
| tesla | 13 | 10 | 427 | **PASS** |

### Observations

* **DIN 70121** — canonical Ioniq blob, the reference we validate against
  real Alpitronics / ABB / Compleo charger traces.
* **ISO 15118-2** — with `EH_` custom-params enabled in the OpenV2G
  codec fork, full handshake goes through.
* **both** — PEV offers DIN + ISO, EVSE picks one. Selection logic
  working.
* **tesla** — Tesla Model Y's specific supportedAppProtocolReq bytes.
  EVSE ACKs. Notable because Tesla-fleet supercharger-spoof attacks
  depend on HotWire's EVSE accepting Tesla's variant.

CD count is consistent across variants (~430 per 20 s) — protocol
negotiation choice doesn't alter steady-state charging rate, as expected.

---

## 6. Back-to-back stress × 8 runs

Each iteration spins up EVSE + PEV, runs 8 s, kills both, checks for
process leaks. No leaks, no regression, no slowdown.

| Run | PEV states | Startup (ms) | EVSE msg types | CD cycles | Clean | Result |
|---|---|---|---|---|---|---|
| #1 | 13 | 2777 | 10 | 129 | yes | **PASS** |
| #2 | 13 | 2665 | 10 | 138 | yes | **PASS** |
| #3 | 13 | 2665 | 10 | 137 | yes | **PASS** |
| #4 | 13 | 2665 | 10 | 137 | yes | **PASS** |
| #5 | 13 | 2664 | 10 | 125 | yes | **PASS** |
| #6 | 13 | 2664 | 10 | 125 | yes | **PASS** |
| #7 | 13 | 2666 | 10 | 126 | yes | **PASS** |
| #8 | 13 | 2667 | 10 | 126 | yes | **PASS** |

### Observations

* **Startup time is rock-stable**: 2664 - 2777 ms, σ ≈ 35 ms. First run
  is 112 ms slower due to cold Python import cache; runs 2-8 are
  clustered within 13 ms of each other.
* **No zombie / orphan processes** after every kill (`pgrep` returns 0).
* **No port-bind conflicts** — `::1:57122` TCP binds cleanly on every
  re-run, showing `SO_REUSEADDR` + proper socket teardown in
  `fsmEvse.Tcp` / `HotWireWorker.shutdown()`.
* **No mid-session slowdown**: CD count varies only 125-138 across runs,
  which is pure 8 s window jitter, not drift.
* **No sdp_server thread leak** either; that was a Checkpoint 11
  concern at the time and the stress test exercises it 8× without
  accumulation.

---

## What this matrix validates

1. **Timing / retry behaviour is correct**: phase2 retries at 2 s, budget
   timeout is honoured, no crashes.
2. **IPv6 scope handling is robust**: SdpServer binds correctly on scope
   0 and scope 2, auto-detects the right link-local address.
3. **Full DIN 70121 state machine completes** across voltage / duration /
   protocol axes — no parameter combination breaks the 13-state flow.
4. **No resource leaks over repeated sessions** — stable startup time
   and clean process teardown.
5. **Four protocol variants all land in the same steady state**,
   confirming the EXI codec handles DIN, ISO-15118-2, and Tesla
   supportedAppProtocol blobs.

## What this matrix does NOT validate

* Real PLC hardware SLAC completion (no peer modem).
* Real TCP/IPv6 handoff from SDP advertisement to V2G socket (SDP
  simulation fakes this by writing `::1:57122` directly).
* Real charging-station compatibility (Alpitronics / ABB quirks).
* Real Arduino hardware-interface serial parsing (stub-real mode used).

The Checkpoint 19 real-hardware session already covered SLAC +
SDP; full phase4 on real modems is still unverified.

---

## Reproduction

```bash
# Single-session demo
./scripts/sim_loopback.sh 25

# Voltage × duration matrix
./scripts/sim_matrix.sh

# Protocol variants
./scripts/sim_protocol_matrix.sh

# Back-to-back stress
./scripts/sim_stress_matrix.sh 8

# Phase1 duration sweep
for dur in 2 5 10; do
  sudo python3 scripts/hw_check/phase1_link.py -i eth0 --duration "$dur" --min-frames 0
done

# Phase2 budget + role sweep
for role in pev evse; do
  for budget in 3 8; do
    sudo python3 scripts/hw_check/phase2_slac.py -i eth0 --role "$role" --budget "$budget"
  done
done

# Phase3 scope sweep
for role in pev evse; do
  for scope in 0 2; do
    sudo python3 scripts/hw_check/phase3_sdp.py -i eth0 --role "$role" --scope-id "$scope" --budget 5
  done
done
```
