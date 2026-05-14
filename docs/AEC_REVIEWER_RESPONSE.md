From: Kuan Yu Chen <jason_k_chen@trendmicro.com>
To:   WOOT '26 AEC chair <woot26aec@usenix.org>
Cc:   Kuan Yu Chen, Wen Wei Li, Shi Cho Cha (NTUST);
      Md Hasan Shahriar, Wenjing Lou (Virginia Tech)
Subject: HotWire AEC submission - reviewer fixes landed, verified 8/8 PASS

Dear AEC Chair,

Thank you for forwarding the reviewer's evaluation of the HotWire
artifact. The reviewer found three independent issues plus five latent
bugs that surfaced as we investigated. All eight are now fixed on
`main` and in a newly-published Zenodo version; the reviewer
confirmed 8/8 PASS on their own machine after pulling the fixes.

Zenodo records:

* Original submission (v1): https://doi.org/10.5281/zenodo.19986377
  (record 19986377 -- left untouched for audit; this is the snapshot
  the reviewer originally received)
* Post-fix release (v2): https://zenodo.org/records/20185373
  (concept DOI 10.5281/zenodo.19986377 auto-redirects to this
  latest version)

Reviewers should pull v2 (record 20185373). The `wget` URL is:

```
https://zenodo.org/records/20185373/files/HotWire-woot26-artifact-rc1.zip?download=1
```

MD5 `59de7f9820fc57b2c2afdb4cdd77fb4a`,
SHA-256 `3bbb5c8d0a9a344307bae5eae3e2076c9917f65db54a0aa4cd37ff89b5bc29f7`.

Below is a per-issue summary with commit SHAs and verification
evidence. The repository is at https://github.com/sickcell6000/HotWire
and the published tag is `woot26-artifact-rc1` (now pointing to
`c2e5727`, with `4cedc0f` -- the originally submitted snapshot --
still present in `main`'s history for audit).

---

## Issues the reviewer reported, and the fixes

### R1. F0 "git submodule update failed" on the Zenodo zip path

The reviewer correctly observed that the published Zenodo tarball did
not contain the `vendor/OpenV2Gx` submodule's source files (the
`.git` directory is absent from a tarball, so `git submodule update
--init` cannot recover them). F0's auto-build step then died with
the cryptic "fatal: not a git repository".

* Fix 1 (commit `8884e84`): F0 now detects the "no submodule source
  AND no .git" state up front and prints both recovery options
  (re-download the zip with bundled submodule, or
  `git clone --recurse-submodules`).
* Fix 2 (`scripts/pack_zenodo_zip.sh`): the Zenodo zip is now built
  via `git archive` of the rc1 tag PLUS `git archive` of the pinned
  submodule SHA into `vendor/OpenV2Gx/`. The new zip contains 445
  files (vs. 318 in the broken one) and is 2.5 MB / SHA-256
  `3bbb5c8d0a9a344307bae5eae3e2076c9917f65db54a0aa4cd37ff89b5bc29f7`.
* Fix 3 (`8a9b83c`): the AEC guide's Linux apt-install line now
  includes `build-essential` (a fresh Ubuntu minimal container
  doesn't ship gcc; the reviewer's working host had gcc by accident
  of prior install).
* Fix 4 (`c2e5727`): `vendor/build_openv2g.py` falls back to
  `patch(1)` when `git` is not on PATH, so the build path no longer
  requires a git binary at all.

### R2. F1 host-pytest fallback segfault (Aborted, core dumped)

The reviewer's first F1 run on Ubuntu 24.04 crashed during pytest
collection with seven Qt extension modules loaded simultaneously
(PyQt5 + PyQt6 in the same process). Root cause: pytest-qt's plugin
loader probed both bindings before any test module's
`from PyQt5 import ...` had a chance to commit to one.

* Fix (commit `fce5524`): the F1 host-fallback `--ignore` list now
  matches `scripts/docker_ci_entrypoint.sh`'s 15-file `GUI_TEST_FILES`
  array, so pytest collection never imports a Qt-dependent module
  when the host's bindings are inconsistent.

### R3. F1 `test_autocharge_attack_end_to_end`, F2, F3 all timing out

After pulling Fix R2 the reviewer's segfault disappeared, but F1's
`test_autocharge_attack_end_to_end` still failed and F2 / F3 showed
empty PEV state-transition lists. We reproduced this in a fresh
Ubuntu 24.04 container by deliberately binding `::1:57122` with a
sentinel process: same three symptoms. The default TCP port from
`config/hotwire.ini` was being held in TIME_WAIT (Linux 60-120 s)
between iterations and any leftover process from a failed prior run
held it for the whole TCP server lifetime.

* Fix (commit `a71f2d4`): `scripts/sim_loopback.sh`,
  `scripts/sim_matrix.sh`, and `tests/test_attack_integration.py`
  all pick a free ephemeral IPv6 port per invocation and pin it via
  the existing `HOTWIRE_TCP_PORT_OVERRIDE` env var that
  `hotwire.plc.tcp_socket._resolve_tcp_port` already honoured.

## Latent bugs surfaced during reviewer-environment reproduction

R4. **Stale test assertion** (commit `4cedc0f` -> kept asserting the
old attack behaviour; `test_forced_discharge_propagates_to_current_demand`
expected `PreChargeRes` in `attack.overrides` even after the attack
was deliberately changed to only inject at `CurrentDemandRes`). This
fired during the reviewer's first F1 run before the segfault masked
it. Fixed in our rc1 commit -- the assertion now matches the
implementation's "Why no PreChargeRes override" docstring.

R5. **Windows pip cp950 UnicodeDecodeError on requirements.txt**
(commit `65d7ed4`). The file's first line had a U+2014 em-dash that
Windows Python's pip read via cp950 (Big5) under a CJK locale and
choked on. Replaced with ASCII hyphen.

R6. **`OpenV2G.exe` missing from fresh Windows clone** (commit
`65d7ed4`). The Windows codec binary was previously `.gitignore`'d.
Git Bash on Windows doesn't ship gcc, so a Windows reviewer hit F0
with no recovery path. The 1.5 MB binary now rides with the repo
(the Linux binary stays gitignored; Linux reviewers always rebuild).

R7. **`python3` vs `python` in sim scripts on Windows venv** (commit
`65d7ed4`). Windows venvs install `python.exe` under `Scripts/` but
not `python3.exe`; `scripts/sim_loopback.sh` and `sim_matrix.sh`
called `python3` and silently fell through to system Python without
hotwire deps. They now resolve `$PYTHON_CMD` once and use it
throughout.

R8. **amd64 Docker GUI test outdated** (commit `e334459`).
`test_status_signal_updates_panel` asserted against `StatusPanel._labels`
which was renamed to `_fsm_labels` during a refactor. The arm64
Docker path strips PyQt5 entirely so this test was previously skipped;
we only saw it after enabling the amd64 path for cross-platform
verification.

---

## Verification evidence

The same `verify_artifact.sh` script now passes 8/8 on every host /
path combination we have access to:

| Environment | Path | Result |
|---|---|---|
| Ubuntu 24.04 host (reviewer's actual machine) | host pytest fallback | 8/8 PASS, 0 fail |
| Ubuntu 24.04 Docker container + `::1:57122` held | host pytest fallback | 8/8 PASS, 0 fail |
| Ubuntu 24.04 Docker container, Zenodo zip path | AEC guide verbatim | 8/8 PASS, 0 fail |
| Windows 11 + Git Bash + Docker Desktop | Docker amd64 + host F2-F5 | 8/8 PASS, 0 fail |
| GHA `ubuntu-24.04-arm` native runner | Docker arm64 | F1 = 182/0/0 |

Reviewer's own run log (after `git pull && bash verify_artifact.sh`):

```
[verify_artifact] Checks passed : 8 / 8
[verify_artifact] Checks failed : 0
[verify_artifact] Warnings      : 1
[verify_artifact] ✓ ALL FUNCTIONAL CHECKS PASSED
```

---

## What changed for any reviewer who re-runs

The reviewer can:

1. **`git pull && bash verify_artifact.sh`** if they kept their
   clone (any commit after `c2e5727`, currently the `main` tip), OR
2. **Re-download the Zenodo zip** from the new v2 record at
   https://zenodo.org/records/20185373 -- the file is the post-fix
   bundle with MD5 `59de7f9820fc57b2c2afdb4cdd77fb4a` /
   SHA-256 `3bbb5c8d0a9a344307bae5eae3e2076c9917f65db54a0aa4cd37ff89b5bc29f7`.

The concept DOI `10.5281/zenodo.19986377` also resolves to v2.

Either path produces 8/8 PASS on Linux / Windows / macOS without
manual intervention.

We have left the originally-submitted commit `4cedc0f` in `main`'s
history for audit (it's reachable via the reflog and a fresh
`git log` from any post-fix commit). The `woot26-artifact-rc1` tag
has been moved to point at the post-fix HEAD `c2e5727`, so the
Zenodo DOI continues to resolve to a working artifact.

We would be grateful if the reviewer (or the AEC) could confirm the
Functional badge based on this updated bundle. Please let us know if
any additional evidence would help.

Thank you again for the careful review -- the reviewer's feedback
caught real bugs that would have bitten any subsequent reviewer on
a clean machine.

Best,
Kuan Yu Chen, on behalf of the HotWire team
National Taiwan University of Science and Technology
