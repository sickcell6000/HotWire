# Findings from the test matrix

This file documents the bugs the comprehensive matrix surfaced during
artifact preparation, plus the patches that landed for each. All
fixes are in HEAD; the matrix bundles in this directory contain
**both pre-fix and post-fix evidence** so reviewers can see the
diagnostic chain.

## Bug 1 — `HotWireWorker.shutdown()` leaked the EVSE TCP listening socket

**Test:** `test7_stress/` (1/10 PASS) → `test10_stress_postfix/` (5/5 PASS).

### Symptom

Running back-to-back `HotWireWorker` instances in one Python process,
the first iteration completes a full V2G session, then every
subsequent iteration's PEV peer can only emit `supportedAppProtocolReq`
— the EVSE reply never arrives.

### Root cause

`HotWireWorker.shutdown()` originally stopped only the SDP server
thread. The `fsmEvse.Tcp` listening socket stayed bound; even with
`SO_REUSEADDR` on the next socket, the kernel kept routing incoming
connections to the previous socket (still in its accept queue). The
PEV's TCP connect succeeded against a half-dead peer that immediately
closed.

Per-iteration SLAC timing was stable across all 10 rounds (~0.05 s),
so the modem layer was always fine — the bug lived in pure-Python
socket lifecycle.

### Fix

Two surgical changes in `hotwire/plc/tcp_socket.py` +
`hotwire/core/worker.py`:

1. `pyPlcTcpServerSocket.shutdown()` — new method, closes every client
   socket plus the listening socket cleanly. Idempotent.
2. `HotWireWorker.shutdown()` — now calls the TCP server's `shutdown()`,
   disconnects any PEV-side TCP client, and drops FSM references so
   GC reclaims the rest.

Plus a follow-up in `hotwire/gui/worker_thread.py` so the GUI's
`QtWorkerThread.stop()` actually invokes `worker.shutdown()` (it
previously only stopped the QThread loop, leaking the same socket).

### Verification

After the fix, 5 iterations × 25 s budget on the live bench:

```
PEV  5/5 PASS  mean elapsed 14.5 s, stdev 0.8 s
EVSE 5/5 PASS  mean elapsed 14.1 s, stdev 0.1 s
```

## Bug 2 — `PauseController.get_pending()` returned a shallow copy

A GUI editor pre-populated from `get_pending()` could mutate the
inner `params` dict and accidentally rewrite the FSM's stored
defaults. Fix: `get_pending()` now does a one-level-deep copy.

Test: `tests/test_pause_controller.py::test_get_pending_returns_copy`.

## Bug 3 — `accuVoltage` defaulted to 0 V

The synthetic battery in `RealHardwareInterface` had `accuVoltage = 0`,
so PEV `PreChargeReq.EVTargetVoltage` went out as 0 V. EVSE then
fell through to its 350 V hardcoded default → constant 350 V vs 0 V
gap → PEV stuck in PreCharge.

Fix: `accuVoltage` reads `charge_target_voltage` from config (220 V on
this rig), matching the EVSE's seed.

## Bug 4 — EVSE `EVTargetVoltage` parser only handled the nested shape

OpenV2G emits flat dotted keys (`EVTargetVoltage.Value: "220"`); the
parser only checked the nested `{EVTargetVoltage: {Value: ...}}` form
and fell through to a 350 V hardcoded default.

Fix: try flat keys first, keep nested as fallback for older codecs.

## Bug 5 — phase4 prepair vs worker-internal SLAC raced

A Checkpoint-20 `_prepair_modem` step ran SLAC + CM_SET_KEY before
the worker started. Worker-internal SLAC (Checkpoint 19) did the same
thing using the same config-pinned NMK/NID, so we ended up running
SLAC twice. The modem locked into the prepair AVLN and ignored the
worker's fresh M-SOUNDs, manifesting as 'PARAM.REQ sent 15× peer
saw 0' even though phase2 standalone proved both modems can pair.

Fix: gate `_prepair_modem` behind `HOTWIRE_PHASE4_PREPAIR=1`
(default off). `test5_prepair_gate/` reproduces the original failure
on demand to validate the gate is needed.

## Bug 6 — EVSE early-exit before PEV could accumulate `min_cd`

`phase4_v2g`'s `_early_pass` returned True the moment EVSE emitted
`PowerDeliveryRes`, ~5 s in. The PEV peer was still ramping its
CurrentDemand loop; the early socket close starved its CD counter
and the PASS criterion failed even though the V2G stack was healthy.

Fix: EVSE early-exit additionally requires `min_cd` `CurrentDemandRes`
responses, so the PEV has time to satisfy its own pass criterion.

## Summary

| Test | Result |
|---|:---:|
| 1 — A1 EVCCID Impersonation | ✅ |
| 2 — A2 voltage matrix (220/500/777 V) | ✅ 3/3 |
| 3 — Multi-stage pause | ✅ |
| 4 — Abort path | ✅ |
| 5 — `HOTWIRE_PHASE4_PREPAIR=1` gate | ✅ (gate behaves) |
| 6 — PEV-side override | ✅ |
| 7 — 10× back-to-back stress (pre-fix) | ❌ 1/10 (Bug 1) |
| 8 — Concurrent override + pause-edit | ✅ |
| 9 — Fuzz, 5 rounds | ✅ 5/5 |
| 10 — Stress (post-fix) | ✅ 5/5 |
| 11 — Long-running 3 min | ✅ RSS +0 % post-warmup |

Six bugs found, six fixed, one regression test per bug landed in
`tests/`.
