# test1_a1_evccid — Attack A1 evidence summary

This bundle is a real-hardware capture of **Attack A1 (Autocharge /
EVCCID impersonation)** running between a HotWire-controlled rogue
PEV and a HotWire-controlled rogue EVSE in cooperative mode. The
purpose is to demonstrate that the A1 attack code path actually
fabricates an attacker-supplied EVCCID and transmits it on the
DIN 70121 wire — the same code path that, against a real Autocharge
station, lets an attacker bill a victim's account (HITCON ZeroDay
ZD-2025-00559).

## What you should see

The PEV-side override controller was loaded with the sentinel EVCCID
**`deadbeefcafe`** (clearly synthetic — would never collide with a
legitimate manufacturer-assigned MAC). Grep the session for it:

```bash
$ grep -E "EDA_[0-9a-f]+" pev/session.jsonl
{"ts": "2026-04-27T15:04:30.818109+00:00", "kind": "phase4.trace",
 "message": "[PEV] SessionSetupReq: encoding command EDA_deadbeefcafe"}
```

`EDA_<hex>` is the OpenV2G EXI codec's positional command for "encode
SessionSetupReq with this EVCCID byte string". The attacker-supplied
value is `deadbeefcafe`, not the PEV's actual MAC.

The EXI-encoded `SessionSetupReq` going on the wire (29 bytes) is in
the next trace line; the corresponding PCAP frame is in
`pev/phase4_capture.pcap` (open in Wireshark with the HomePlug AV /
V2GTP dissector and look at the second TCP segment after the SDP +
TCP handshake).

## What this proves

- The HotWire pause-controller / override layer can replace
  `SessionSetupReq.EVCCID` between PEV's FSM and the EXI encoder
  (the operator-supplied value reaches the wire unchanged).
- The peer EVSE accepts it without any cryptographic check, then
  replies with `SessionSetupRes.ResponseCode = OK_NewSessionEstablished`
  (visible in the next `rx`-direction `phase4.message` line).
- Together these establish that **at any DIN-70121 / Autocharge
  station that authenticates by EVCCID alone, an attacker who has
  obtained a victim's EVCCID can open a billed session as that
  victim**. This is the substance of HITCON ZD-2025-00559.

## Capture metadata

| Field | Value |
|---|---|
| Captured at | 2026-04-27T15:04:30 UTC |
| Capture host | Raspberry Pi 4B + QCA7005 PLC modem (PEV side) |
| Counterpart | Windows 11 host + QCA7005 (EVSE side) |
| Sentinel EVCCID | `deadbeefcafe` (synthetic) |
| Real HV bus voltage during capture | 0 V (no battery; lab bench) |
| PCAP | `pev/phase4_capture.pcap` (HomePlug AV) |
| Decoded trace | `pev/session.jsonl` (one DIN msg per line) |
| Run config | `pev/config.json` |

## In the paper

Maps to §4 (A1 attack mechanism) and §6 (field-test deployment).
Counterpart paper claim: a HotWire-controlled rogue PEV can transmit
an arbitrary EVCCID and the receiving EVSE will accept it without
challenge.

## Cross-reference

For the simulation-mode counterpart (no hardware needed), see
`tests/test_attack_sim_mode.py::test_a1_autocharge_impersonation_sim`
— it sends the same sentinel `deadbeefcafe` over an in-process IPv6
loopback and asserts the EVSE side decodes the same forged EVCCID.
