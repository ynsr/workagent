#!/usr/bin/env bash
# Print the diff hash used for plan staleness checks.
# Usage: diff_hash.sh <default-branch> [repo-dir]
# Covers the branch diff vs the merge-base with origin/<default>, excluding plan files.
set -euo pipefail
DEFAULT="${1:?default branch required}"
cd "${2:-.}"
git diff --no-color "origin/${DEFAULT}...HEAD" -- . ':(exclude).workagent/prodtest' | sha256sum | awk '{print "sha256:"$1}'
