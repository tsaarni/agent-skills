#!/bin/bash
# Install skills and prompts for Kiro CLI.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
skills=~/.kiro/skills
prompts=~/.kiro/prompts

mkdir -p ~/.kiro
rm -rf "$skills" "$prompts"
ln -sfn "$repo/skills" "$skills"
echo "Linked skills -> $skills"
ln -sfn "$repo/prompts" "$prompts"
echo "Linked prompts -> $prompts"
