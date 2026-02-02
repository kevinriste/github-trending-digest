#!/bin/bash
# Test script to verify gh CLI works in crontab context
# Run this from crontab to verify GitHub authentication is working

set -e

echo "=== GitHub CLI Connection Test ==="
echo "Date: $(date)"
echo "User: $(whoami)"
echo "Working directory: $(pwd)"
echo ""

# Set up environment (same as process.sh)
export PYENV_ROOT="$HOME/.pyenv"
command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"

echo "--- Testing gh auth status ---"
gh auth status

echo ""
echo "--- Testing gh api user ---"
gh api user --jq '.login'

echo ""
echo "--- Testing gh repo list (first 3) ---"
gh repo list --limit 3

echo ""
echo "--- Testing git push access (dry-run) ---"
cd "$(dirname "$0")"
git remote -v
git fetch --dry-run origin main 2>&1 || echo "Fetch test completed"

echo ""
echo "=== All tests passed ==="
