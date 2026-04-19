# HotWire Datasets

This directory is a **placeholder for the anonymised capture corpus
that will be released alongside the paper** when the coordinated
disclosure embargo expires (see Section 10 of the paper and
`docs/dataset.md` for the full catalogue).

During review, only a small set of **preview captures** is published
here — chosen so reviewers can exercise the HotWire toolkit end-to-end
against real vehicle data without us releasing any fields that could
identify a specific vehicle or charging station.

## Preview captures (`preview/`)

| File | Size | Contents | Why it's safe |
|------|-----:|----------|---------------|
| `preview/IONIQ6_good.pcapng` | 64 KB | Full SLAC handshake (10 MNBC\_SOUND + `CM_ATTEN_CHAR` + `CM_SLAC_MATCH`) from a Hyundai IONIQ6 against a commercial EVSE | Contains only Layer-2 HomePlug AV frames (EtherType 0x88E1) — no EVCCID / EVSEID / SessionID (those live in the DIN 70121 application layer, not in SLAC). MAC addresses are preserved because SLAC's attenuation pairing is worthless without them, but they are not personally identifying in isolation. |

### Verify integrity

```bash
# Linux / macOS
sha256sum datasets/preview/*.pcapng

# Windows PowerShell
Get-FileHash datasets\preview\*.pcapng -Algorithm SHA256
```

Expected:

```
2ff13b1cdc17f560e55697175a77f385157901a8c36a4a6b91b208cab1c97501  IONIQ6_good.pcapng
```

### Exercise it

```bash
python -m pytest tests/test_slac_attenuation.py -v
# Expected: 3 tests pass — including replay of IONIQ6_good.pcapng through
# the SLAC state machine, reaching SLAC_PAIRED.
```

---

## Full corpus (embargoed)

The full capture corpus (~25 MB across 11 files) is documented in
`docs/dataset.md` with SHA256 manifest and per-file description. It
includes:

- Three IONIQ6 SLAC captures (`IONIQ6.pcapng`,
  `IONIQ6_good.pcapng` — published above — and
  `IONIQ6_withBigLoading.pcapng`)
- One Tesla Model Y session ending at PreCharge
  (`teslaEndWithPrecharge.pcapng`) — the **negative result** the paper
  cites (Tesla rejects the forced-discharge attack)
- Six Volvo EX30 (platform code "N7") sessions of varying duration
  (1 min, 10 min, 20 min, 1 h 30 min, plus two successful end-to-end
  runs) — the **positive discharge result** quoted in §6
- One Volvo EX30 CableCheck-phase regression fixture
  (`testSuccessN7toCableCheck.pcapng`)

### Redaction contract

Before publication, every file in the full corpus will be processed by
`scripts/redact_session.py` (after first being converted to JSONL via
`tshark` — see the recipe in `docs/dataset.md` §"Redaction before
publication"). The redactor's stable-hash contract means a given EVCCID
always maps to the same `<EVCCID_xxx>` tag within one publication
round, letting readers confirm "yes, same vehicle across captures"
without ever seeing the true MAC.

### Embargo release plan

Per the paper's Section 10:

> We will embargo the toolkit release until 180 days after initial
> disclosure or until 75% of disclosed vulnerabilities have confirmed
> patches deployed to production systems, whichever occurs first.

The same timeline applies to this `datasets/` directory. When it
opens:

1. A single `datasets/full/` directory will be populated with the
   redacted corpus
2. The SHA256 manifest at `docs/dataset.md` will be frozen and
   referenced by the paper's DOI
3. This `README.md` will be updated with the permanent Zenodo /
   Figshare DOI for the corpus so the paper can cite a stable
   reference

---

## License

The preview capture is released under the same GPL-3.0 licence as the
HotWire toolkit. You are free to use it for academic research,
teaching, or security evaluation under the copyleft terms of that
licence.
