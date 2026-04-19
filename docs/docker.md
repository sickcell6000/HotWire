# HotWire in Docker

HotWire ships a CI-grade Docker image that:

1. **Rebuilds the OpenV2Gx EXI codec from source** (proves byte-for-byte
   reproducibility — the Windows `.exe` you see in the tree is not what
   the container runs against).
2. **Runs the full 35-module test suite**, including GUI tests under
   PyQt6 offscreen rendering — no X server, no Xvfb.
3. **Produces HTML coverage, JUnit XML, hw_check JSONL + pcap artifacts**
   on the host filesystem via bind mounts.

The image is deliberately Debian-slim + Python 3.12 + PyQt6, roughly
1.1 GB uncompressed. The code paths it exercises match the Raspberry
Pi target architecturally (Linux + Python + libpcap), so a green run
here is a strong signal the Pi will behave.

---

## Quickstart

From the repo root:

```bash
# One-shot CI-grade run — build, test, exit.
docker compose run --rm hotwire-ci
```

Expected exit code `0`. Artifacts land in three bind-mounted dirs:

| Host path | What's inside |
|---|---|
| `./htmlcov/index.html` | Line-by-line coverage of `hotwire/` |
| `./reports/regression-junit.xml` | Per-test JUnit output |
| `./reports/gui-junit.xml` | GUI-smoke JUnit output |
| `./reports/coverage.xml` | Cobertura XML (Codecov / GitLab friendly) |
| `./runs/<ts>/` | hw_check dry-run phase 0 / 0.5 artifacts |

Open `./htmlcov/index.html` in a browser for the coverage view.

---

## Watch mode — test-on-save

Docker 24+ supports `compose watch`:

```bash
docker compose watch
```

Pick the `hotwire-dev` profile (the compose file auto-selects it under
the `watch` profile). On every save of a file under `hotwire/`, `tests/`,
`scripts/`, or `config/`, the container:

1. Syncs the changed file into `/work/...` (no image rebuild)
2. Restarts the entrypoint, re-running the test matrix
3. Updates `./htmlcov/` so your browser refresh shows the new coverage

If you change `vendor/`, `requirements.txt`, or `Dockerfile`, the
image rebuilds automatically.

### Pipe to `fg` or `tail` for continuous output

```bash
docker compose logs -f hotwire-dev
```

---

## Skipping phases

The entrypoint is controlled by four env vars:

| Var | Default | Meaning |
|---|---|---|
| `HOTWIRE_CI_CODEC` | `1` | Golden-fixture verify (22 encoder cases) |
| `HOTWIRE_CI_HW_CHECK` | `1` | hw_check orchestrator dry-run |
| `HOTWIRE_CI_GUI` | `1` | GUI-smoke subset, fails early on Qt runtime issues |
| `HOTWIRE_CI_REGRESSION` | `1` | Full pytest suite + coverage |

Override at compose level, e.g. skip the slow full regression while
iterating on a GUI widget:

```bash
HOTWIRE_CI_REGRESSION=0 docker compose run --rm hotwire-ci
```

Or in a shell-out for a single concern:

```bash
docker compose run --rm -e HOTWIRE_CI_CODEC=0 \
                        -e HOTWIRE_CI_HW_CHECK=0 \
                        -e HOTWIRE_CI_REGRESSION=0 \
                        hotwire-ci
# → runs only the GUI smoke subset
```

---

## CI integration (GitHub Actions, GitLab CI, Jenkins, …)

Minimal GitHub Actions skeleton:

```yaml
# .github/workflows/test.yml
name: tests
on: [push, pull_request]
jobs:
  docker-ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive
      - uses: docker/setup-buildx-action@v3
      - name: Run HotWire test matrix
        run: docker compose run --rm hotwire-ci
      - name: Upload HTML coverage
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: coverage-html
          path: htmlcov/
      - name: Publish JUnit
        if: always()
        uses: test-summary/action@v2
        with:
          paths: reports/*.xml
```

The one-shot invocation makes this drop-in for any CI that can run
`docker compose` — no HotWire-specific steps needed.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Failed to build codec-builder` | Confirm submodules are checked out: `git submodule update --init --recursive` |
| `GUI test fails with xcb plugin not found` | Image already installs `libxcb-*` — delete the dangling image: `docker image rm hotwire:ci` then re-run |
| `psutil.net_io_counters` returns empty | Container networking is expected to be minimal; hw_check phases 1-4 SKIP, not FAIL |
| `docker compose watch` does nothing | Docker < 24 doesn't support it — upgrade Docker Desktop or use `docker compose run --rm hotwire-ci` in a shell loop |
| `Permission denied` on artifact dirs | The container writes as root; on Linux you may need `chown -R $USER:$USER htmlcov/ reports/ runs/` after the first run, or add `user: "${UID}:${GID}"` to the service |

---

## What Docker does NOT cover

- **Real PLC hardware tests** — no QCA7005 modem in the container,
  so phases 1–4 of hw_check SKIP. Those are validated on the Pi.
- **Attack CLI against a real charger** — obvious non-goal.
- **Mac / ARM hosts** — the image is linux/amd64. If you're on an
  Apple Silicon Mac, Docker will emulate under Rosetta 2; slower but
  works. Pass `--platform linux/amd64` explicitly if buildkit complains.

For real-hardware validation, see `docs/REPRODUCING.md` step 7 and
`scripts/hw_check/README.md`.
