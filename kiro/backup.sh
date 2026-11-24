#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${SCRIPT_DIR}"
SOURCE_BASE="${HOME}/.kiro"

echo "Backing up Kiro CLI configuration to ${TARGET_DIR}..."

# Agent profiles: every ~/.kiro/agents/*.json and *.md (skips .bak backups
# and the .json.example file).
mkdir -p "${TARGET_DIR}/agents"
for src in "${SOURCE_BASE}"/agents/*.json "${SOURCE_BASE}"/agents/*.md; do
  [[ -f "${src}" ]] || continue
  rel_path="agents/$(basename "${src}")"
  # Scrub machine home paths
  sed "s|${HOME}|~|g" "${src}" > "${TARGET_DIR}/${rel_path}"
  echo "  [OK] ${rel_path}"
done

# CLI settings, without the model picker's usage timestamps.
rel_path="settings/cli.json"
src="${SOURCE_BASE}/${rel_path}"
if [[ -f "${src}" ]]; then
  mkdir -p "${TARGET_DIR}/settings"
  jq 'del(.["chat.modelPicker.lastUsed"])' "${src}" | sed "s|${HOME}|~|g" > "${TARGET_DIR}/${rel_path}"
  echo "  [OK] ${rel_path} (dropped chat.modelPicker.lastUsed)"
else
  echo "  [WARN] File not found: ${src}" >&2
fi

echo "Backup complete."
