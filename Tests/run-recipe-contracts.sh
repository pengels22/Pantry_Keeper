#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
recipe_test_dir=$(mktemp -d)
trap 'rm -rf "$recipe_test_dir"' EXIT
swiftc 'Pantry Keeper/RecipeModels.swift' 'Pantry Keeper/RecipeAPI.swift' \
  Tests/RecipeContractTests.swift -o "$recipe_test_dir/recipe-contracts"
"$recipe_test_dir/recipe-contracts"
