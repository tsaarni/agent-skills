#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${HOME}/.config/gravelpit/policies"
TARGET_DIR="${SCRIPT_DIR}/policies"

if ! compgen -G "${SOURCE_DIR}/*" >/dev/null; then
  echo "Skipping Gravelpit backup (no policies in ${SOURCE_DIR})."
  exit 0
fi

mkdir -p "${TARGET_DIR}"
cp "${SOURCE_DIR}"/* "${TARGET_DIR}/"
echo "Backed up Gravelpit policies to ${TARGET_DIR}."
