# HotWire Datasets

This document catalogs the real-hardware packet captures HotWire's test
suite uses for SLAC replay (`tests/test_homeplug_slac_replay.py`,
`tests/test_slac_attenuation.py`) and for manual DIN / ISO 15118-2
dissection practice. All captures were taken from commercial chargers
and production electric vehicles; none include personally-identifying
information in the wire bytes.

## Why it lives outside the repo

The corpus lives at `../EVSEtestinglog/EV_Testing/` — one directory
above the HotWire repo root — rather than inside the repo, for two
reasons:

1. **Size** — the largest capture (`N7_PULL_Energy_Susscessful_Stop_After_1and30hours.pcap`) is ~19 MB and the full set is ~25 MB; committing them would triple the clone size for the ~99% of users who only need to run tests or reproduce the paper's software claims.
2. **Embargo** — per the paper's §11 Ethical Considerations, individual capture files will be released in a coordinated disclosure window. Keeping them out-of-tree lets the codebase ship GPL-3.0 now while the dataset release proceeds on its own schedule.

The tests skip gracefully when the dataset isn't present:

```python
# tests/test_homeplug_slac_replay.py
def _require_capture(path: Path = CAPTURE_PATH) -> Path:
    if not path.exists():
        pytest.skip(f"real-capture pcapng not found at {path}; ...")
```

---

## Expected layout

```
<parent-of-HotWire>/
├── HotWire/                      # this repo
└── EVSEtestinglog/
    └── EV_Testing/
        ├── IONIQ6.pcapng
        ├── IONIQ6_good.pcapng
        ├── IONIQ6_withBigLoading.pcapng
        ├── N7_PULL_Energy_Susscessful.pcap
        ├── N7_PULL_Energy_Susscessful_2.pcap
        ├── N7_PULL_Energy_Susscessful_Stop_After_10min.pcap
        ├── N7_PULL_Energy_Susscessful_Stop_After_1and30hours.pcap
        ├── N7_PULL_Energy_Susscessful_Stop_After_1min.pcap
        ├── N7_PULL_Energy_Susscessful_Stop_After_20min.pcap
        ├── teslaEndWithPrecharge.pcapng
        └── testSuccessN7toCableCheck.pcapng
```

Tests compute the capture root as:

```python
_CAPTURE_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "EVSEtestinglog" / "EV_Testing"
)
```

---

## File catalog

| File | Size | Capture | Paper §  |
|------|-----:|---------|----------|
| `IONIQ6.pcapng` | 7.6 KB | Hyundai IONIQ6 SLAC handshake against a commercial CCS charger — minimal capture containing a complete `CM_SLAC_PARAM.REQ` + attenuation sounds + `CM_SLAC_MATCH.REQ`. Used by the parametrized replay test. | §4 Methodology, §6 Evaluation |
| `IONIQ6_good.pcapng` | 64 KB | Full-length IONIQ6 SLAC with all 10 `CM_MNBC_SOUND.IND` frames and `CM_ATTEN_CHAR.RSP` — exercises the 6-state SLAC FSM end-to-end. | §4 Methodology |
| `IONIQ6_withBigLoading.pcapng` | 84 KB | IONIQ6 SLAC with an intentionally high-impedance coupling — used to validate that the state machine doesn't misread attenuation values. | §4 Methodology |
| `teslaEndWithPrecharge.pcapng` | 53 KB | Tesla Model Y session from SLAC through DIN 70121 PreCharge — reference capture for Attack 2 (forced discharge) development. | §4 Attack 2, §6 Evaluation |
| `testSuccessN7toCableCheck.pcapng` | 531 KB | Volvo EX30 (platform code "N7") session from SLAC through CableCheck — used as a regression witness when fixing DIN 70121 FSM bugs. | §6 Evaluation |
| `N7_PULL_Energy_Susscessful.pcap` | 190 KB | Full successful Volvo EX30 charge session terminated at customer request. | §6 Evaluation |
| `N7_PULL_Energy_Susscessful_2.pcap` | 298 KB | Second full charge — same vehicle, different day, different EVSE. Used to confirm deterministic vs vehicle-state-dependent fields. | §6 Evaluation |
| `N7_PULL_Energy_Susscessful_Stop_After_1min.pcap` | 300 KB | Charge stopped at 1 minute — short session. | §6 Attack 2 timing |
| `N7_PULL_Energy_Susscessful_Stop_After_10min.pcap` | 1.8 MB | Charge stopped at 10 minutes. | §6 Attack 2 timing |
| `N7_PULL_Energy_Susscessful_Stop_After_20min.pcap` | 1.7 MB | Charge stopped at 20 minutes. | §6 Attack 2 timing |
| `N7_PULL_Energy_Susscessful_Stop_After_1and30hours.pcap` | 19 MB | Charge stopped at 1h30m — longest capture; stresses session duration fields and timestamp wraparound. | §6 Attack 2 timing, §6 Sustained attack |

---

## SHA256 manifest

Reviewers can verify capture integrity against this manifest. Compute
the hash of each file on your side:

```bash
# Linux / macOS
sha256sum EVSEtestinglog/EV_Testing/*.pcap*

# Windows PowerShell
Get-FileHash EVSEtestinglog\EV_Testing\*.pcap* -Algorithm SHA256
```

And compare against:

```
e496cb408b39a120ecf7427f4d4b72bf7f8a77548cd2820e0189608682d488ca  IONIQ6.pcapng
2ff13b1cdc17f560e55697175a77f385157901a8c36a4a6b91b208cab1c97501  IONIQ6_good.pcapng
21358968609a7db0193335c3df41e8e8c4c1528743cee2135f05870b785590fc  IONIQ6_withBigLoading.pcapng
a7a6c880a837aa6d58acf457912747df43d7c0ef40f15696bed12afec5858d13  N7_PULL_Energy_Susscessful.pcap
9ed7bdc2be3ad8a51ddd74778e4c269b6a36d712d57be04d801ba1dbb0db90c2  N7_PULL_Energy_Susscessful_2.pcap
e40cf346600cdef54548491f9116db35ef0185a73721db6eb27c260a323469b3  N7_PULL_Energy_Susscessful_Stop_After_10min.pcap
e81b644d265ace325846de4061cd888d09a02c5f57b58488b299ce0396758360  N7_PULL_Energy_Susscessful_Stop_After_1and30hours.pcap
5ac4e388044ee49f2157d187cfab0dc8f4be3b72be6bdfc2bbda450643e76af7  N7_PULL_Energy_Susscessful_Stop_After_1min.pcap
99cd92a97674d40c1b873601a2eb25464df38beb996a4a621e0913518b2ec1fd  N7_PULL_Energy_Susscessful_Stop_After_20min.pcap
20eef935899b48350feffab63eab4978fc8d967a1a39cb6a2c871dd7a6c12dc0  teslaEndWithPrecharge.pcapng
4e3e2527473e460d0d11698b540ece16fc1f1d8eb976e273f964d29d341a2450  testSuccessN7toCableCheck.pcapng
```

---

## Redaction before publication

HotWire ships `scripts/redact_session.py` to produce anonymized
derivatives. The script replaces (with stable tags):

- EVCCID — `00:...:01` → `<EVCCID_001>` (same vehicle → same tag)
- EVSEID — `ZZ00000001` → `<EVSEID_001>`
- SessionID — random bytes → `<SessionID_xxx>`
- IPv6 addresses — `fe80::xxxx` → `<IPv6_xxx>`

Current scope: JSONL session logs (from HotWire's own logger). Raw
pcap files from real chargers are NOT auto-redacted — they include
OUI-identifiable MAC addresses and cipher-level fields the tool
does not parse. Before public release, reviewers should:

1. Run `tshark -r input.pcap -Y '!slac && !v2gtp' -w filtered.pcap` to drop non-relevant layers
2. Use `tcprewrite --seed=<n> --srcdnat=... --dstdnat=...` to scramble MACs
3. Confirm no Diffie-Hellman public values or certificate fingerprints remain (not applicable to DIN 70121 which is unsigned, but ISO 15118-2 captures may contain them)

See `docs/attacks.md` §Ethics for the paper's disclosure policy.

---

## Citation

If you use this dataset in a derivative work, cite the paper:

> (Anonymous). *HotWire: Real-World Impersonation and Discharge
> Attacks on Electric Vehicle Charging Systems*. 2026. (Full
> citation will be updated post-acceptance.)

and note the dataset section number from which your captures were
drawn.
