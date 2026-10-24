#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${SCRIPT_DIR}/configs"
SOURCE_BASE="${HOME}/.pi"

CONFIG_FILES=(
  "agent/AGENTS.md"
  "agent/APPEND_SYSTEM.md"
  "agent/keybindings.json"
  "agent/mcp.json"
  "agent/models.json"
  "agent/settings.json"
)

mkdir -p "${TARGET_DIR}"
echo "Backing up Pi Coding Agent configurations to ${TARGET_DIR}..."

for rel_path in "${CONFIG_FILES[@]}"; do
  src="${SOURCE_BASE}/${rel_path}"
  dest="${TARGET_DIR}/${rel_path}"

  if [[ -f "${src}" ]]; then
    mkdir -p "$(dirname "${dest}")"
    if [[ "${rel_path}" == "agent/settings.json" ]]; then
      # Scrub machine home paths
      sed "s|${HOME}|~|g" "${src}" > "${dest}"
      echo "  [OK] ${rel_path} (sanitized home paths)"
    else
      cp "${src}" "${dest}"
      echo "  [OK] ${rel_path}"
    fi
  else
    echo "  [WARN] File not found: ${src}" >&2
  fi
done

echo "Backup complete."
