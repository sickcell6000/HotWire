# Ethics Considerations — evidence map

This document is a reviewer aid: every concrete claim in the paper's
Section 10 "Ethics Considerations" is mapped to the artifact or code
path that substantiates it. Nothing in this file adds to what is in
the paper — it only helps a reviewer (or a PC chair doing an ethics
audit) find the corresponding object quickly.

Paper § references assume the current `paper/10、Ethical Considerations
and Responsible Disclosure.tex`.

---

## Claim-by-claim evidence map

### "We used only our own pre-registered user accounts"

- **Paper text:** *"…we used only our own pre-registered user accounts
  at public charging stations, ensuring no third-party victim was
  financially burdened by our testing."*
- **Evidence:**
  - Attack A1 playbook (`hotwire/attacks/autocharge_impersonation.py`)
    takes the victim EVCCID as an explicit CLI arg; it does not
    "harvest and replay" automatically. Every session therefore
    required a human (us) to type in the EVCCID — no third-party
    victim could be processed by the toolkit unbeknownst to the
    operator.
  - `sessions/` (git-ignored; generated at runtime) hold the JSONL
    logs of every experiment. On request to PC we can share the
    redacted logs with `EVCCID_*` tags that all resolve to the same
    pre-registered account.

### "Session terminated after confirming successful authentication bypass"

- **Paper text:** *"The charging session was terminated after
  confirming successful authentication bypass and energy delivery,
  limiting energy consumption to 1.25 kWh paid through our registered
  account."*
- **Evidence:**
  - `config/hotwire.ini` option `exit_on_session_end = True` — the
    worker exits as soon as the EV side enters SessionStop, i.e. no
    "let it run overnight" code path.
  - `scripts/record_sustained.py` uses `--duration` (seconds), so any
    run is bounded by a pre-declared wall-clock cap; the default 3600
    produces the 1.25 kWh quoted in the paper.
  - JSONL log for the public-station run will be released post-embargo
    under `datasets/full/A1_public_station_1.25kWh.jsonl` (see
    `docs/dataset.md`).

### "Forced discharge experiments on private property"

- **Paper text:** *"The forced discharge attack evaluations were
  conducted entirely within controlled environments on private
  property, with no experiments performed on vehicles in public spaces
  or fleet operations."*
- **Evidence:**
  - `EVSEtestinglog/EV_Testing/` (outside the repo; documented at
    `docs/dataset.md`) is the ground-truth capture corpus for §6. All
    captures were acquired on the authors' institutional test bench.
  - `hardware/schematics/wiring_diagram.md` describes the fixed
    bench layout (RPi 4 + QCA7005 + resistive bank + Arduino sense
    board); there is no mobile variant of the discharge test rig.

### "Multiple safety interlocks, current limiting, automated session termination"

- **Paper text:** *"We implemented multiple safety interlocks,
  including emergency contactors, current limiting circuits, and
  automated session termination triggers set at 10% state-of-charge
  reduction thresholds…"*
- **Evidence map:**

  | Paper claim | Physical implementation |
  |---|---|
  | Emergency contactor | Arduino Uno digital pin **D2** → 2-channel optocoupler relay board (5 V coil, 30 A contact) — see `hardware/schematics/wiring_diagram.md` §"Component list" row 6 |
  | Current limiting | Inherent to 1.25 kW resistive bank at battery voltage (5 × 220 V / 250 W bulbs in parallel) — §"Component list" row 9. 380 V / 1.25 kW ≈ 3.3 A, well under the 60 A interlock threshold |
  | Automated session termination | `scripts/record_sustained.py --duration <s>` enforces a wall-clock bound; the 10 %-SOC threshold is operator-observed from the vehicle dashboard rather than automated (the paper should say "operator-observed" rather than "triggers" — flag for copy-edit) |
  | Galvanic isolation | 1:1 PLC coupling transformers on CP/PE — §"Component list" row 4 |

- **Honest caveat:** the 10% SOC threshold is observed from the EV
  dashboard by the operator, not yet automated by HotWire (no OBD-II
  SOC reader is integrated). This is noted in
  `docs/sustained_attack_runbook.md` §"Safety interlocks this runbook
  depends on" point 3.

### "No vehicle sustained battery damage"

- **Paper text:** *"No vehicle sustained battery damage or experienced
  state-of-charge depletion below manufacturer-recommended safe
  operating limits."*
- **Evidence:**
  - Table 3 in §6: each 60-minute session drained 1.25 kWh (2.1 % of a
    ≥60 kWh pack), far above any deep-discharge threshold.
  - Post-experiment vehicles were re-tested via HotWire's own session
    replay (`scripts/export_pcap.py` + dsV2Gshark dissection) to
    confirm the SLAC + handshake still completed normally.

### "150-day coordinated disclosure"

- **Paper text:** *"We initiated responsible disclosure of all
  discovered vulnerabilities 150 days before publication submission…"*
- **Evidence:**
  - The authors' internal disclosure log (not in the public repo for
    obvious reasons) documents the disclosure dates for each vendor
    and operator. On request to the PC, a redacted timeline can be
    shared.
  - `docs/full_compliance_audit.md` §L's honest-caveat table notes
    this is not publicly verifiable at review time — reviewers who
    want to spot-check are encouraged to contact the listed national
    CERT (next item).

### "Submitted comprehensive vulnerability reports to an East Asian
   national vulnerability reporting platform"

- **Paper text:** *"We also submitted comprehensive vulnerability
  reports to an East Asian national vulnerability reporting platform…"*
- **Evidence:**
  - A CVE or national CVD ticket number will be added to the
    camera-ready version once the embargo is released.
  - At review time this claim is reviewer-must-trust.

### "Open-source release coordinated with affected stakeholders"

- **Paper text:** *"The open-source release of our testing toolkit
  will be coordinated with affected stakeholders to allow sufficient
  remediation time before public availability."*
- **Evidence:**
  - The repository currently lives at the anonymous review URL listed
    in §11. Post-acceptance it migrates to a permanent GitHub
    repository whose URL will replace the anonymous link.
  - `datasets/README.md` specifies the 180-day / 75 % patched
    embargo condition derived from §10.
  - `scripts/redact_session.py` is the pre-release sanitiser; its
    deterministic SHA-256 hash contract is documented in
    `docs/dataset.md`.

---

## For PC / ethics committee contact

If the programme chair or an ethics reviewer needs direct evidence
beyond what the paper and this repo already provide, please contact
the corresponding author. We can provide on request:

1. **Redacted disclosure timeline** — dates of first notification to
   each of the 4 vehicle manufacturers and 7 charging network
   operators
2. **Institutional approval** — our institutional review process
   for on-bench high-voltage experiments (note: the paper does not
   claim IRB review because human subjects were not involved; the
   approvals are for high-voltage lab work)
3. **Pre-registered account proof** — invoice lines from the public
   charging session confirming the energy cost was billed to the
   authors' own account
4. **Unredacted capture pre-embargo** — for verification purposes
   only, under a reviewer NDA

We committed to these in the Ethics section and stand behind the
commitment.

---

## For a skeptical reviewer

If you (the reviewer) suspect any Ethics claim is overstated, the
highest-leverage requests to the authors are:

1. *"Please share the redacted disclosure timeline."* — establishes
   the 150-day claim
2. *"Please share the invoice line for the 1.25 kWh public-station
   session."* — establishes the "own pre-registered account" claim
3. *"Please confirm by institutional affiliation that the listed
   vehicle manufacturers consented to testing."* — establishes the
   "under NDA" claim
4. *"Please provide the CERT/national-CVD ticket number."* —
   establishes the submission claim
5. *"Please run `python scripts/record_sustained.py --duration 3600`
   on an example target and upload the resulting `runs/<ts>/` bundle
   to the artifact evaluation submission."* — confirms A2 really
   sustains across 60 minutes without needing any claim from the
   authors

Requests 1–4 rely on the authors; request 5 is objectively verifiable
and costs the authors ~1 hour once the hardware is wired up.
