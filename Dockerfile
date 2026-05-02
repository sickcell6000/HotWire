# syntax=docker/dockerfile:1.7
#
# HotWire — CI-grade test image
#
# Two stages:
#   1. codec-builder — compiles OpenV2G from vendor/OpenV2Gx sources,
#      reproduces the binary used by the Python codec. Proves the
#      LGPL source we ship is what the tests actually run against.
#   2. runtime — Python 3.12 + PyQt6 (offscreen) + psutil + tcpdump,
#      carrying the freshly-built codec binary.
#
# Everything runs with QT_QPA_PLATFORM=offscreen so GUI tests work
# without Xvfb. No display, no window manager, no hassle.

# =====================================================================
# Stage 1 — build the EXI codec from source
# =====================================================================

FROM debian:12-slim AS codec-builder

# We bypass build_openv2g.py's `git apply` plumbing and use plain
# `patch -p1` (filesystem-only). `patch` supports forward-apply with
# idempotency via a pre-check dry-run, so a tree that's already
# partially patched is handled correctly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc make python3 patch \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY vendor/ ./vendor/
RUN mkdir -p hotwire/exi/codec

# Normalise permissions — Windows clones can leave stray +x bits.
RUN find vendor/OpenV2Gx -type f \( -name '*.c' -o -name '*.h' -o -name '*.patch' \) \
    -exec chmod 644 {} +

# Normalise line endings — Windows clones serialize .c/.h as CRLF, but
# the patches were generated on Linux with LF. Convert everything to
# LF before patching so `patch -p1` doesn't trip on "different line
# endings" errors.
RUN find vendor/OpenV2Gx -type f \( -name '*.c' -o -name '*.h' \) \
    -exec sed -i 's/\r$//' {} +

# Apply patches forward if possible; skip cleanly if already applied.
# `--dry-run -R` returns 0 when the patch has already been applied,
# in which case we move on without touching the tree.
RUN set -e; \
    cd vendor/OpenV2Gx; \
    for p in ../patches/*.patch; do \
        [ -f "$p" ] || continue; \
        name=$(basename "$p"); \
        if patch --dry-run -R -p1 < "$p" >/dev/null 2>&1; then \
            echo "[patch] $name: already applied, skipping"; \
        else \
            echo "[patch] $name: applying"; \
            patch --forward --batch --no-backup-if-mismatch -r - -p1 < "$p"; \
        fi; \
    done

# Invoke build_openv2g's inner compile+install, monkeypatching
# `_apply_patches` to a no-op so it doesn't re-enter git-apply logic.
RUN python3 -c "import sys; \
sys.path.insert(0, 'vendor'); \
import build_openv2g as b; \
b._apply_patches = lambda dry_run=False: None; \
binary = b.build('gcc', dry_run=False); \
b.install(binary)" \
 && test -x hotwire/exi/codec/OpenV2G \
 && ls -la hotwire/exi/codec/


# =====================================================================
# Stage 2 — runtime (tests, hw_check dry-run, GUI smoke)
# =====================================================================

FROM python:3.12-slim AS runtime

# System deps:
#   - libgl1 + libxkbcommon0 + libegl1 + libfontconfig1 + libdbus-1-3:
#       minimal Qt offscreen runtime
#   - tcpdump: required by hw_check phase1_link + LivePcapViewer
#   - libpcap0.8 + libpcap-dev: pypcap runtime + headers to build wheel
#   - iputils-ping: ping6 for IPv6 multicast check
#   - gcc + python3-dev: to build pypcap from source (no prebuilt wheel on 3.12)
#   - git: `git describe` during tests
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libxkbcommon0 libegl1 libfontconfig1 libdbus-1-3 \
    libglib2.0-0 \
    libxcb-cursor0 libxcb-xkb1 libxkbcommon-x11-0 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
    libxcb-render-util0 libxcb-shape0 libxcb-sync1 libxcb-util1 \
    libxcb-xfixes0 libxcb-xinerama0 \
    tcpdump libpcap0.8 libpcap-dev iputils-ping \
    gcc python3-dev \
    git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# Install Python deps first, so the layer caches when only source changes.
# pypcap is skipped inside the container — Docker can't access a PLC
# modem anyway, and the latest pypcap doesn't build cleanly on Python
# 3.12. The hotwire code imports it lazily and falls back to
# simulation when missing, which is exactly what we want for CI.
COPY requirements.txt ./
RUN grep -v '^pypcap' requirements.txt > requirements.docker.txt \
 && pip install --no-cache-dir -r requirements.docker.txt \
 && pip install --no-cache-dir pytest-cov

# Source tree (excluding the build artifacts .dockerignore filters out).
COPY . ./

# Pull the freshly-built codec from the builder stage so the container
# runs against a Linux-native binary (not the Windows .exe that ships
# in the tree).
COPY --from=codec-builder /build/hotwire/exi/codec/OpenV2G \
    /work/hotwire/exi/codec/OpenV2G
RUN chmod +x /work/hotwire/exi/codec/OpenV2G

# Qt needs a writable HOME for its configuration cache even offscreen.
ENV HOME=/tmp/hotwire
ENV QT_QPA_PLATFORM=offscreen
ENV HOTWIRE_CONFIG=/work/config/hotwire.ini
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Output directory for coverage + JUnit — compose bind-mounts these.
VOLUME ["/work/runs", "/work/reports", "/work/htmlcov"]

# Entrypoint orchestrates: regression → hw_check dry-run → GUI smoke →
# HTML coverage. See scripts/docker_ci_entrypoint.sh.
RUN chmod +x scripts/docker_ci_entrypoint.sh
ENTRYPOINT ["bash", "scripts/docker_ci_entrypoint.sh"]
