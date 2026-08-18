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

echo "GitHub Trending Digest--Ensure Postgres service is running"
docker compose up -d postgres

# OpenAI key + model for HN comment camps analysis.
# The global /etc/profile.d/podcast-transcribe.sh (sourced by our `bash -l`) exports a
# now-deactivated OPENAI_API_KEY. Override it with the dedicated good key, and pin the
# prod model (COMMENT_BRIEFING_MODEL defaults to gpt-5-mini in code otherwise).
OPENAI_KEY_ENV="/home/flog99/dev/openai-key/podcast-transcribe.env"
if [ -f "$OPENAI_KEY_ENV" ]; then
    set -a
    . "$OPENAI_KEY_ENV"
    set +a
else
    echo "WARNING: OpenAI key file $OPENAI_KEY_ENV not found; HN comment analysis will be skipped"
fi
export COMMENT_BRIEFING_MODEL="${COMMENT_BRIEFING_MODEL:-gpt-5.6-luna}"

echo "GitHub Trending Digest--Run trending digest script"
uv run python3 trending_digest.py

echo "GitHub Trending Digest--End Script (success)"
