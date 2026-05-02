# HotWire — Safety and Authorization

HotWire can emulate either side of a high-voltage DC charging protocol.
Used against simulation or lab equipment it is safe. Used against real
vehicles or charging stations it interacts with systems that can deliver
**400 V DC at 200 A or more**. This document is the mandatory read before
any hardware engagement.

## Authorization

**Do not connect HotWire to a vehicle or charging station you do not own
or have explicit written authorization to test against.** This applies
regardless of the attack and regardless of simulation mode — even an
innocuous-looking SessionSetupReq constitutes unauthorized access in
many jurisdictions. See the Computer Fraud and Abuse Act (US), the
Computer Misuse Act (UK), §303a StGB (Germany), 刑法 358 (Taiwan), and
analogous statutes elsewhere.

The paper's evaluation was conducted with:

- **Individual informed consent** from each vehicle owner, in writing,
  for a fixed test window
- **Prior coordination** with each charging network operator, including
  off-peak scheduling and operator staff on-site
- **Institutional Review Board (IRB) approval** covering both the
  reconnaissance (EVCCID capture) and impersonation phases

Your deployment of HotWire needs the same before any live hardware is
involved.

## Electrical risks

HotWire is primarily a protocol tool, but **the CCS charging port it
connects to carries the battery pack voltage** any time the contactors
are closed — and Attack 2 is specifically designed to trick a vehicle
into closing them against an attacker-controlled load. Minimum PPE and
infrastructure for live-hardware testing:

- Appropriate **high-voltage-rated gloves** (Class 0 / 1000 V minimum)
  and an arc-flash-rated face shield when the load side is energised
- An **emergency disconnect** wired to break both DC legs, operable
  one-handed from outside the vehicle
- **Galvanic isolation** between the HotWire host computer and the DC
  side (1:1 isolation transformers on CP and PLC pairs; optocoupler-
  isolated relays for any current-sensing feedback)
- **Over-current and over-voltage protection** sized for the expected
  load — the paper's 1.25 kW testbed uses 100 W incandescent bulbs in
  parallel with fast-blow fuses and a hall-effect current sensor
  feeding a hardware interlock
- **Thermal monitoring** on any resistive load running longer than a
  few minutes
- **Fire suppression** (Class C rated for electrical) within reach
- A designated **safety observer** whose only role is to hit the
  emergency disconnect

Do not run Attack 2 ("forced discharge") against any vehicle without
the owner present and the vehicle in park with the 12 V key removed.

## Protocol-layer ethics

Even protocol-only actions — e.g. reading a parked stranger's EVCCID —
are almost always treated as unauthorized access. HotWire's trace and
session-log files contain plaintext EVCCIDs and session identifiers
that can be used to impersonate the victim: treat these files as
**sensitive personal data** and destroy them when your research is
complete.

The paper's evaluation results (Table 3, §6) are anonymised: no raw
EVCCID values or station identifiers are published. Our data-release
plan follows the same rule — if you extend HotWire and release your
own datasets, redact the same fields.

## Disclosure timeline

If you discover a new vulnerability with HotWire:

1. **Do not publish.** Write up your finding and identify the
   responsible vendor or network operator.
2. Share the finding with them through their **coordinated disclosure
   channel** (security@\<vendor\>, their bug bounty program, or ISAC
   Auto if they have no public contact). Give a clear reproducer and
   any HotWire logs that demonstrate the issue.
3. Offer a **reasonable embargo** (90 days is the industry norm for
   safety-critical auto issues; longer is appropriate for issues
   requiring a hardware recall).
4. Publish **after** the embargo expires or the vulnerability is fixed
   in deployed vehicles, whichever is later.

## GPL-3.0 and dual-use

HotWire is GPL-3.0-or-later. That license ensures the tool stays open
and that improvements come back to the community — it does not change
the ethical or legal obligations above. Publishing a derivative of
HotWire on a public git forge without the attached SAFETY.md is not a
GPL violation, but it is a research-ethics violation.

---

*This document is not a substitute for legal advice. Consult counsel
before live-hardware experimentation in any jurisdiction.*
