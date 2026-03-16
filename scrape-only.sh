#!/bin/bash
# Lightweight scrape-only runner: scrapes GitHub trending and stores snapshots.
# No LLM, no digest generation, no git push, no email.
set -e

export PYENV_ROOT="$HOME/.pyenv"
command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"

cd "$(dirname "$0")"

docker compose up -d postgres

uv run python3 trending_digest.py --scrape-only
