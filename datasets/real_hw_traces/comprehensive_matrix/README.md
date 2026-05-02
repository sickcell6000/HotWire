# Comprehensive Software-and-Hardware Test Matrix

Eleven end-to-end tests against the live Pi-PEV / Windows-EVSE bench
exercising every distinct PauseController code path. Each test ships
a Pi-side pcap so reviewers can replay the wire activity.

| # | Test | Path | Verifies | Status |
|---|------|------|----------|:---:|
| 1 | A1 EVCCID Impersonation | `test1_a1_evccid/` | PEV-side `set_override` on `SessionSetupReq.EVCCID` | ✅ |
| 2 | A2 Voltage Matrix | `test2_a2_voltage/` | EVSE-side `set_override` at 220 / 500 / 777 V | ✅ |
| 3 | Multi-stage Pause | `test3_multi_pause/` | Two stages blocked + edited + released in one session | ✅ |
| 4 | Abort Path | `test4_abort/` | `PauseController.abort()` releases FSM with original params; V2G advances to PowerDelivery | ✅ |
| 5 | Prepair Gate | `test5_prepair_gate/` | `HOTWIRE_PHASE4_PREPAIR=1` re-enables the gated step (reproducing the original SLAC clash on demand) | ✅ |
| 6 | PEV-side Override | `test6_pev_override/` | PEV-side `set_override` symmetric to EVSE; `EVTargetVoltage=999` reaches wire | ✅ |
| 7 | Stress (10 iter, pre-fix) | `test7_stress/` | Worker reuse within one Python process | ❌ 1/10 — bug found, see [FINDINGS.md](./FINDINGS.md) |
| 8 | Concurrent attacks | `test8_concurrent/` | `set_override` + `set_pause_enabled` active simultaneously | ✅ |
| 9 | Fuzz, 5 rounds | `test9_fuzz/round{1..5}/` | Random EVCCID / V (1–2000) / I (0–500), each round fresh process | ✅ 5/5 |
| 10 | Stress (5 iter, post-fix) | `test10_stress_postfix/` | Same as 7 against patched worker | ✅ 5/5, mean 14.5 s |
| 11 | Long-running (3 min) | `test11_long_running/` | RSS / FD leak during sustained CurrentDemand | ✅ post-warmup RSS +0 %, 1891 msgs |

## Bundle layout

Each test directory contains:

- `*.pcap` — Pi-side wire capture (Wireshark + dsV2Gshark plugin)
- `session.jsonl` — every decoded message keyed by stage + direction
- `config.json` — host context snapshot

For the why-and-how of each test, the script that produced it is in
`scripts/hw_check/phase{4..10}*.py`. The runners are self-documenting
(module docstring describes PASS criteria).

## Bugs surfaced

[FINDINGS.md](./FINDINGS.md) — seven bugs the matrix surfaced during
artifact preparation, plus the patches that landed.

## Re-running locally

These bundles were captured at HEAD; reviewers without the bench can
either inspect the pcaps in Wireshark directly, or pair two hosts
running the matching `phaseN_*.py` runner from `scripts/hw_check/`.
