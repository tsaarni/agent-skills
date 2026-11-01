#!/bin/bash
# Install prompts for GitHub Copilot in VS Code.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
if [[ $(uname) == Darwin ]]; then
    prompts=~/Library/"Application Support"/Code/User/prompts
else
    prompts=~/.config/Code/User/prompts
fi

# Prompts become *.prompt.md, $ARGUMENTS -> ${input:args}.
rm -rf "$prompts"
mkdir -p "$prompts"
for f in "$repo"/prompts/*.md; do
    sed 's/\$ARGUMENTS/${input:args}/g' "$f" > "$prompts/$(basename "$f" .md).prompt.md"
done
echo "Converted prompts -> $prompts/"
