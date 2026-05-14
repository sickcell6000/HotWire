#!/usr/bin/env bash
#
# Pack the Zenodo artifact zip WITH submodule contents.
#
# The previous Zenodo upload (HotWire-woot26-artifact-rc1.zip, 1.35 MB)
# was created by `git archive` and shipped an EMPTY vendor/OpenV2Gx/
# directory. Any reviewer following the AEC guide would fail at F0 with
# "OpenV2G source missing".
#
# This script produces a zip that includes:
#   - all tracked files at the woot26-artifact-rc1 tag
#   - all tracked files in the vendor/OpenV2Gx submodule at its pinned SHA
#
# Output: ./dist/HotWire-woot26-artifact-rc1.zip (~5-6 MB expected)
#
# Run on Windows: bash scripts/pack_zenodo_zip.sh
# Run on Linux  : bash scripts/pack_zenodo_zip.sh

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# Default: pack from the rc1 tag (the Zenodo-published snapshot).
# Override with first arg to pack from any ref, e.g.:
#   bash scripts/pack_zenodo_zip.sh main      # pack current main HEAD
#   bash scripts/pack_zenodo_zip.sh HEAD      # pack working tree's HEAD
REF="${1:-woot26-artifact-rc1}"
TOPDIR="HotWire-woot26-artifact-rc1"           # zip top-dir name stays constant
OUT="dist/${TOPDIR}.zip"
STAGING="dist/_staging"

echo "[pack] cleaning staging area"
rm -rf "${STAGING}" "${OUT}"
mkdir -p "${STAGING}/${TOPDIR}" "$(dirname "${OUT}")"

echo "[pack] exporting root tracked files at ${REF}"
git archive --format=tar --prefix="${TOPDIR}/" "${REF}" \
    | tar -x -C "${STAGING}"

echo "[pack] exporting submodule vendor/OpenV2Gx tracked files"
SUBMODULE_SHA=$(git -C vendor/OpenV2Gx rev-parse HEAD)
echo "[pack]   submodule SHA: ${SUBMODULE_SHA}"
git -C vendor/OpenV2Gx archive --format=tar --prefix="${TOPDIR}/vendor/OpenV2Gx/" "${SUBMODULE_SHA}" \
    | tar -x -C "${STAGING}"

echo "[pack] sanity check: vendor/OpenV2Gx populated?"
test -f "${STAGING}/${TOPDIR}/vendor/OpenV2Gx/src/test/main.c" \
    || { echo "[pack] FAIL: submodule still empty"; exit 1; }

echo "[pack] sanity check: top-level Dockerfile present?"
test -f "${STAGING}/${TOPDIR}/Dockerfile" \
    || { echo "[pack] FAIL: Dockerfile missing"; exit 1; }

echo "[pack] zipping"
( cd "${STAGING}" && zip -rq "${ROOT}/${OUT}" "${TOPDIR}" )

echo "[pack] cleanup staging"
rm -rf "${STAGING}"

echo
echo "[pack] ===== DONE ====="
echo "[pack] output: ${OUT}"
echo "[pack] size:   $(du -h "${OUT}" | cut -f1)"
echo "[pack] entries:"
unzip -l "${OUT}" | tail -2
echo
echo "Next: upload ${OUT} to the Zenodo draft record"
echo "      https://zenodo.org/uploads (replace previous zip)"
