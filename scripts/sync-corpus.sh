#!/usr/bin/env bash
# Syncs the public pl-pii-bench corpus from the product repo's internal bench
# directory into this standalone repo. This is a plain file copy (rsync
# --delete), never a symlink and never a submodule pointer into the product
# repo, so this repo stays self-contained and publishable on its own.
#
# Source of truth: products/anonimator/bench/corpus/public/ and its sibling
# annotation-guidelines.md, both inside the desktop monorepo that this repo
# is a sibling directory of. This script does not read or write anything
# else under products/anonimator/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# pl-pii-bench/ is a sibling of products/ inside the desktop monorepo.
SOURCE_ROOT="$(cd "${REPO_ROOT}/../products/anonimator/bench/corpus" && pwd)"
SOURCE_PUBLIC="${SOURCE_ROOT}/public"
SOURCE_GUIDELINES="${SOURCE_ROOT}/annotation-guidelines.md"
DEST="${REPO_ROOT}/corpus"

if [ ! -d "${SOURCE_PUBLIC}" ]; then
    echo "error: source corpus not found at ${SOURCE_PUBLIC}" >&2
    exit 1
fi
if [ ! -f "${SOURCE_GUIDELINES}" ]; then
    echo "error: annotation guidelines not found at ${SOURCE_GUIDELINES}" >&2
    exit 1
fi

mkdir -p "${DEST}"

echo "Syncing ${SOURCE_PUBLIC} -> ${DEST}"
rsync -a --delete \
    --exclude ".DS_Store" \
    "${SOURCE_PUBLIC}/" "${DEST}/"

echo "Syncing ${SOURCE_GUIDELINES} -> ${DEST}/annotation-guidelines.md"
cp "${SOURCE_GUIDELINES}" "${DEST}/annotation-guidelines.md"

echo "Done. Lanes synced:"
find "${DEST}" -mindepth 1 -maxdepth 1 -type d | sort
