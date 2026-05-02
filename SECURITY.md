# Security and Malicious-Operations Notice

> **Required reading** before running any part of the HotWire toolkit
> outside of its Docker simulation path. This file complements
> [`SAFETY.md`](SAFETY.md) (which covers *electrical* and *authorization*
> concerns) and addresses the *cyber-security* properties of the code
> itself.

---

## What HotWire is, exactly

HotWire is an **offensive security research artifact**. Its purpose is
to generate CCS DIN 70121 / ISO 15118-2 protocol frames — including
deliberately fabricated or spoofed frames — and transmit them onto a
wire or a loopback socket.

Two of its modules are specifically designed to produce frames whose
only valid use is to mislead a peer:

### Module A1 — `hotwire/attacks/autocharge_impersonation.py`

Crafts a `SessionSetupReq` carrying an arbitrary EVCCID supplied by the
operator. When sent to a CCS charging station whose Autocharge feature
associates a session with the EVCCID value (without further
authentication), the station will authorize the session under whatever
billing account is linked to that EVCCID.

**If run against a public production charging station, this causes the
station to bill energy to the owner of the provided EVCCID without
their authorization.** That is a crime in most jurisdictions. See
`SAFETY.md` §Authorization for the specific statutes.

### Module A2 — `hotwire/attacks/forced_discharge.py`

Crafts `PreChargeRes` and `CurrentDemandRes` messages that claim a
voltage within a few volts of the vehicle's current battery-pack
voltage. In vehicles whose BMS trusts this protocol reading over its
own inlet voltage sensor, the BMS responds by closing its high-voltage
contactors — connecting the battery pack to what it believes is a
matched external rail, but is in fact a 0 V (or attacker-controlled)
load.

**If run against a real vehicle without a safety load bank, emergency
disconnect, and current-limiting interlocks, this can result in
uncontrolled battery discharge into attacker-controlled hardware.**
See `SAFETY.md` §Electrical risks.

---

## Public disclosure record

The two attack scenarios HotWire demonstrates have been formally
reported through **HITCON ZeroDay**, Taiwan's national vulnerability
coordination platform, and are now in public-disclosure status:

| Attack | HITCON ZeroDay ID | Affected vendors | Risk | Submitted | Public |
|---|---|---|---|---|---|
| **A1** — Autocharge / EVCCID impersonation | [`ZD-2025-00559`](https://zeroday.hitcon.org/vulnerability/ZD-2025-00559) | LUXGEN 納智捷 N7 + 8 charging-network operators (Charging Blok / 區快充, EVALUE / 華城電能, EVOASIS / 源點科技, iCHARGING / 中興電工, STAR Charger / 星舟快充, TAIL / 特爾電力, U-POWER / 旭電馳科研, YES！來電 / 裕電俥電) | High | 2025-06-11 | 2025-08-11 |
| **A2** — Forced-discharge / BMS V2L bypass | [`ZD-2025-00560`](https://zeroday.hitcon.org/vulnerability/ZD-2025-00560) | LUXGEN 納智捷 N7 | High | 2025-06-11 | 2025-08-11 |

Both reports were triaged on 2025-06-13, forwarded to the affected
vendors on 2025-06-19, and moved to public-disclosure status on
2025-08-11 by the HITCON ZeroDay platform after the coordinated
remediation window closed. Reporter: `sickcell`.

This artifact is being released in line with that public-disclosure
status — the vulnerabilities have already been disclosed for ≥ 8
months by the time of WOOT '26 artifact submission.

## Why this artifact is being released

Brief timeline and rationale:

- **2025-06-11** — Both findings reported through HITCON ZeroDay.
- **2025-06-13** — HITCON ZeroDay triage complete.
- **2025-06-19** — Reports forwarded by HITCON to all listed vendors.
- **2025-06–07** — HITCON-coordinated remediation window (60 days,
  per the platform's standard timeline for High-risk submissions).
- **2025-08-11** — Both reports moved to public-disclosure status by
  the HITCON ZeroDay platform after the remediation window closed.
  This is the official end of the embargo.
- **2026-05** — This artifact is released, **8+ months after public
  disclosure**.

### Vendor remediation status

The HITCON ZeroDay platform tracks per-vendor patching state on the
public report pages (linked above). At the time of artifact release
(2026-05), the platform shows **"未回報修補狀況 / 未複測"** ("vendor
has not reported remediation status / not retested") for both
ZD-2025-00559 and ZD-2025-00560. We have no further private
information about vendor fixes beyond what the platform shows.

This is consistent with the platform's policy: after the coordinated
window closes the report becomes public regardless of vendor
acknowledgement, so as not to indefinitely block researchers and
defenders from learning about the issue.

### Why we still release the artifact

Even with the public disclosure done eight months ago, releasing the
HotWire toolkit itself is independently justified by the WOOT '26
artifact-evaluation requirements (open-source, AEC-reviewable
research artifacts) and by the broader value to defenders:

1. Other charging-network operators not on the original disclosure
   list can use HotWire to **test their own deployments** for both
   classes of issue.
2. Other vehicle manufacturers can use Module A2 in a sim-mode load
   bank to **regression-test their BMS firmware** against
   protocol-driven contactor-close requests.
3. Independent researchers can **reproduce and extend** the work
   without re-implementing the DIN 70121 stack from scratch.

Release serves three purposes:

1. Enable **other defenders** to validate their own products against
   these attack paths.
2. Enable **other researchers** to reproduce and extend the work.
3. Enable **AEC / artifact reviewers** to evaluate the research
   contributions without having to independently reverse-engineer the
   protocol.

---

## How to use the artifact safely

### Safe paths (no real hardware touched)

- `docker compose run --rm hotwire-ci` — runs 240 unit + integration
  tests entirely inside a container.
- `./scripts/sim_loopback.sh` — runs a full DIN 70121 session between
  two HotWire processes on `::1` IPv6 loopback. No packet leaves the
  host.
- `./scripts/sim_matrix.sh` / `sim_protocol_matrix.sh` / `sim_stress_matrix.sh`
  — parametric simulation matrices. Same loopback isolation.
- Reading any file under `hotwire/`, `scripts/`, `docs/`, `tests/`.

**These are the paths used for the USENIX WOOT '26 Artifact Evaluation
Functional badge check. Use these unless you have explicit authorization
to do otherwise.**

### Paths that touch real hardware (require authorization)

- `scripts/hw_check/phase0_*.py` — preflight against a configured
  network interface. **Read-only**, no frames emitted.
- `scripts/hw_check/phase1_link.py` — passive pcap sniff.
  **Read-only**, no frames emitted.
- `scripts/hw_check/phase2_slac.py` — emits real `CM_SLAC_PARAM.REQ`
  and `CM_SET_KEY.REQ` onto the wire. Requires a PLC modem, and will
  attempt to pair the modem with any peer modem within range.
- `scripts/hw_check/phase3_sdp.py` — emits real UDP/15118 multicast.
- `scripts/hw_check/phase4_v2g.py` — runs the full V2G state machine,
  including the attack payloads if configured.
- `scripts/run_evse.py --hw` / `scripts/run_pev.py --hw` — same, for
  long-running impersonation sessions.

**These paths must not be pointed at infrastructure you do not own or
have written authorization to test. `SAFETY.md` §Authorization is not
optional.**

---

## For the USENIX AEC specifically

The artifact evaluation path documented in `docs/README.md` uses only the
"Safe paths" above. The AEC does not need to, and **should not**,
exercise the `--hw` paths or any `scripts/hw_check/phase[234]_*.py`
script against real hardware. No operational capability is lost in
the evaluation by restricting to simulation — the framework behavior
validated in simulation is the same behavior that runs on real
hardware; what differs is only the L2 transport.

If any AEC member has concerns about evaluating this artifact, please
contact the chair (`woot26aec@usenix.org`). The authors will assist
via the chair while preserving reviewer anonymity.

---

## If you discover a security issue in HotWire itself

HotWire is research software. It is not hardened against adversarial
input. Do not deploy it in any role where a hostile party can speak
CCS-like bytes to it.

If you find a crash, a hang, or a memory-safety issue in HotWire itself
(not a DIN 70121 vulnerability we're demonstrating — those are
documented in the paper), please file an issue on the public
repository or contact the authors via the AEC chair during the
evaluation period.
