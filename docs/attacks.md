# HotWire Attack Playbooks

This document describes the two attack playbooks bundled with HotWire.
Each corresponds directly to a numbered attack in the paper.

Read **[SAFETY.md](../SAFETY.md)** before running any attack against real
hardware.

---

## A1 — Unauthorized Autocharge (EVCCID Impersonation)

**Paper reference:** §4 Attack 1.
**Implementation:** [`hotwire/attacks/autocharge_impersonation.py`](../hotwire/attacks/autocharge_impersonation.py),
[`scripts/attacks/autocharge_impersonation.py`](../scripts/attacks/autocharge_impersonation.py).

### What it does

Runs HotWire as a PEV (electric vehicle emulator) and overrides the
outbound `SessionSetupReq.EVCCID` field to a previously-captured 12-hex
identifier. When connected to an Autocharge-enabled station, the
station's backend matches the EVCCID to the victim's registered account
and authorizes the session, billing all energy delivered to the victim.

### Setup

Phase 1 (reconnaissance) is a separate step — HotWire helps by **displaying
the EVCCID on the EVSE StatusPanel** the moment a vehicle connects, but
does not currently automate the capture. Connect HotWire as an EVSE to
the target vehicle, wait for the handshake to reach `SessionSetup`,
copy the EVCCID from the UI (`EVCCID:` field in the live-parameters
panel), and disconnect.

Phase 2 (impersonation) is what this playbook automates.

### CLI

```bash
# With GUI (recommended for demos — shows the attack live):
python scripts/attacks/autocharge_impersonation.py --evccid D83ADD22F182

# Headless (for fleet testing or CI):
python scripts/attacks/autocharge_impersonation.py --evccid D83ADD22F182 --headless

# Against real hardware (after SAFETY.md review):
python scripts/attacks/autocharge_impersonation.py --evccid D83ADD22F182 --hw
```

### What happens under the hood

```python
# hotwire/attacks/autocharge_impersonation.py
class AutochargeImpersonation(Attack):
    mode = C_PEV_MODE
    overrides = {
        "SessionSetupReq": {"EVCCID": evccid.lower()},
    }
```

At startup, `run_gui()` calls `attack.apply(window.pause_controller)`.
Every subsequent `SessionSetupReq` from the FSM goes through
`PauseController.intercept`, which merges the override onto the default
`{"EVCCID": self.evccid}` — so the wire payload always carries the
spoofed identifier.

### Clarification vs. the paper

The paper describes this attack as *"MAC address spoofing at the firmware
level through direct modem register manipulation"*. HotWire implements
the equivalent at the protocol layer — rewriting the `EVCCID` field
inside the EXI-encoded `SessionSetupReq` before transmission. From the
charging station's backend perspective the two approaches are
indistinguishable; both present the victim's EVCCID. On real hardware
you additionally need to ensure the HomePlug modem's SLAC MAC matches,
because some stations check the layer-2 MAC against EVCCID — HotWire's
current simulation layer doesn't perform that check, but the real-
hardware driver (when finished) will need to program the modem's PIB
alongside this playbook.

### Test coverage

`tests/test_gui_dual_scenarios.py` scenario 4 installs this override via
`pause_controller.set_override("SessionSetupReq", {"EVCCID": ...})` and
verifies the EVSE side's StatusPanel sees the spoofed identifier in the
received `SessionSetupReq`.

---

## A2 — Unauthorized Energy Extraction (Forced Discharge)

**Paper reference:** §4 Attack 2.
**Implementation:** [`hotwire/attacks/forced_discharge.py`](../hotwire/attacks/forced_discharge.py),
[`scripts/attacks/forced_discharge.py`](../scripts/attacks/forced_discharge.py).

### What it does

Runs HotWire as an EVSE and overrides `PreChargeRes.EVSEPresentVoltage`
to a value close to the victim's battery voltage, regardless of what the
real DC pins are carrying (typically 0 V in the attack setup). A
vulnerable BMS — one that trusts the protocol assertion over its own
hardware voltage sensors — believes pre-charge has completed, closes its
high-voltage contactors, and exposes the battery to the attacker's
resistive load.

### Setup

**This attack requires physical high-voltage gear on the load side.**
See SAFETY.md. The software-only path is safe to run against another
HotWire PEV over loopback — it proves the protocol lie propagates, but
doesn't actually move electrons.

1. Select a target battery voltage near what the victim EV operates at.
   Table from the paper:
   - Luxgen n7: ~350 V
   - CMC (unpublished): ~380 V
   - Hyundai IONIQ 6: ~400 V
   - Tesla Model Y: not vulnerable (BMS has hardware voltage sensor)
2. Wire the resistive load bank between the CCS DC+ and DC- pins of the
   attacker-controlled CCS connector. Match load rating to the battery:
   the paper uses 1.25 kW (five 220 V / 250 W bulbs in parallel).
3. Launch the attack *before* connecting the CCS cable to the victim:

### CLI

```bash
# Software-only demo:
python scripts/attacks/forced_discharge.py --voltage 380

# Against real hardware (after SAFETY.md review):
python scripts/attacks/forced_discharge.py --voltage 380 --hw
```

### What happens under the hood

```python
# hotwire/attacks/forced_discharge.py
class ForcedDischarge(Attack):
    mode = C_EVSE_MODE
    overrides = {
        "PreChargeRes": {"EVSEPresentVoltage": voltage},
    }
```

Every `PreChargeRes` the EVSE FSM emits has its `EVSEPresentVoltage`
replaced with the operator-supplied value. The PEV's FSM checks
`abs(EVSEPresentVoltage − EVTargetVoltage) < u_delta_max_for_end_of_precharge`
(configured in `hotwire.ini`, default 10 V); if the override is within
that tolerance of whatever the PEV requested, the PEV exits precharge,
closes contactors, and starts `CurrentDemand`.

### Test coverage

`tests/test_gui_dual_scenarios.py` scenario 3 programmatically handles
the pause-intercept dialog, replaces `EVSEPresentVoltage` with 999, and
verifies the decoded `PreChargeRes` on the PEV side contains
`"EVSEPresentVoltage.Value": "999"`.

### Extending

Sustained discharge requires lying about `EVSEPresentVoltage` and
`EVSEPresentCurrent` **throughout the CurrentDemand loop**, not just
during PreCharge. The current `ForcedDischarge` playbook handles
PreCharge only; to extend:

1. Add a `CurrentDemandRes` override to `forced_discharge.py`.
2. Update `fsm_evse.py`'s CurrentDemand command builder to consume the
   new params (currently it uses bare `EDi` defaults — see
   `stage_schema.STAGE_SCHEMAS_EVSE["CurrentDemandRes"]`, which is
   empty).
3. Pick wire-level values that avoid tripping the PEV's
   `is_error_evse_status_code` check in `constants.py`.

This is in the paper's methodology but not currently in the toolkit —
track as a future-work item.

---

## Composing attacks

Playbooks are pure data; you can combine them by unioning their
`overrides` dicts if they touch different stages. A future
`hotwire/attacks/combined.py` could express e.g. "run A1 + lie about
EVSEMaximumVoltageLimit in ChargeParameterDiscoveryRes to downgrade
session parameters" as a single playbook. Contributions welcome.

---

## Debugging

- **Trace log (right panel of the GUI)** shows every FSM transition
  and EXI command string — the command string includes the override
  params, so you can verify they're being applied by reading the
  `encoding command` line.
- **Req/Res tree view (left panel)** shows every decoded message from
  both sides — confirm the PEV / station actually *received* your
  override by finding the mirror message (`rx` column for incoming).
- **Session log** (`sessions/<mode>_<timestamp>.jsonl`) is written by
  default when launching through the GUI; tail it with
  `Get-Content sessions/EVSE_20260418_*.jsonl -Wait` on PowerShell or
  `tail -f` on Unix.
