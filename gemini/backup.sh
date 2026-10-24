#!/usr/bin/env bash
set -euo pipefail

# Resolve script and target directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${SCRIPT_DIR}/configs"
SOURCE_BASE="${HOME}/.gemini"

# Relative file paths to back up from ~/.gemini/
CONFIG_FILES=(
  "GEMINI.md"
  "settings.json"
  "antigravity-cli/settings.json"
  "config/mcp_config.json"
)

echo "Backing up Antigravity configuration files to ${TARGET_DIR}..."

for rel_path in "${CONFIG_FILES[@]}"; do
  src="${SOURCE_BASE}/${rel_path}"
  dest="${TARGET_DIR}/${rel_path}"

  if [[ -f "${src}" ]]; then
    mkdir -p "$(dirname "${dest}")"
    if [[ "${rel_path}" == "antigravity-cli/settings.json" ]]; then
      # Strip local workspace paths and username leaks for public Git
      jq '.trustedWorkspaces = []' "${src}" > "${dest}"
      echo "  [OK] ${rel_path} (sanitized trustedWorkspaces)"
    else
      cp "${src}" "${dest}"
      echo "  [OK] ${rel_path}"
    fi
  else
    echo "  [WARN] File not found: ${src}" >&2
  fi
done

echo "Backup complete."
