# HotWire patches to OpenV2Gx

These `.patch` files sit on top of upstream
[uhi22/OpenV2Gx](https://github.com/uhi22/OpenV2Gx) (pinned at commit
`1ecbedd`) and are applied automatically by `vendor/build_openv2g.py`
before compilation.

Keeping the diffs as separate files means:

* The submodule pointer stays at an untouched upstream commit, so
  pulling upstream fixes is a `git submodule update` away.
* Every HotWire-specific change to the codec is reviewable as a
  plain unified diff.
* Anyone can rebuild byte-for-byte with `python vendor/build_openv2g.py`.

## `01-hotwire-custom-params.patch`

Two pieces of content live in this patch:

1. **Operator-controllable encode commands.** Originally contributed by
   the pyPLC community, these extensions let every `encode*Response()`
   function accept the full set of DIN 70121 fields from the CLI. Without
   this, the EVSE side of HotWire could only flip a few fields per stage
   and had no way to drive attack playbooks that rely on, for example,
   setting `EVSEPresentVoltage` to an arbitrary value. The patch also
   bumps `NUM_OF_ADDITIONAL_PARAMS` from 5 to 30 and swaps the argv
   parser for a `strtok`-based implementation so 27-arg commands fit.

2. **`EDG` (PreChargeRequest) `EVTargetCurrent` override.** The upstream
   binary hardcodes `EVTargetCurrent = 1 A` regardless of CLI args. This
   patch teaches `encodePreChargeRequest()` to pick up arg index 3 when
   present, falling back to 1 A otherwise — preserving the 3-arg form for
   anything still calling it the old way.

## Rebuilding

```
python vendor/build_openv2g.py
```

The script:

1. Applies every `vendor/patches/*.patch` to `vendor/OpenV2Gx/` in
   lexicographic order (skipping any patch already applied).
2. Compiles the full tree with the auto-detected GCC.
3. Installs the resulting binary into `hotwire/exi/codec/OpenV2G.exe`
   (or `OpenV2G` on Linux/macOS).

`tests/_golden_openv2g.json` pins 22 representative encoder outputs so
regressions in a rebuild are caught before they reach the FSM tests.
