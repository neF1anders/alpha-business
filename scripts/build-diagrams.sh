#!/usr/bin/env bash
# Validate docs/workspace.dsl and export every view to docs/diagrams/*.mmd (Mermaid).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$root/docs/workspace.dsl"
output="$root/docs/diagrams"

structurizr-cli validate -workspace "$workspace"
rm -rf "$output"
structurizr-cli export -workspace "$workspace" -format mermaid -output "$output"
echo "Готово: $output"
