#!/bin/bash

FIRST_LOG_DATE=$1
RUN_LOG=${RUN_LOG:-}
if [ -n "$RUN_LOG" ]; then
    mkdir -p "$(dirname "$RUN_LOG")"
    timestamp_output() {
        while IFS= read -r line; do
            printf '%s %s\n' "$(TZ='America/Chicago' date +%FT%T.%3N%:z)" "$line"
        done
    }
    # Mirror stdout/stderr to the per-run log for reliable error reporting.
    exec > >(tee -a "$RUN_LOG" | timestamp_output) 2>&1
fi

# Enable the script to exit if any command returns a non-zero status
set -e

echo "GitHub Trending Digest--Start Script"

# Set up environment
export PYENV_ROOT="$HOME/.pyenv"
command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"

# Change to project directory (script's directory)
cd "$(dirname "$0")"

echo "GitHub Trending Digest--Install dependencies"
uv sync

echo "GitHub Trending Digest--Run trending digest script"
uv run python3 trending_digest.py

echo "GitHub Trending Digest--End Script (success)"
