#!/usr/bin/env bash
# Daily AI research digest — dispatch to Telegram
set -e

cd /tmp/hermy_repo/Projects/recherche
export $(cat .env | grep -v '^#' | xargs)

python3 run.py "artificial intelligence machine learning" --max 10 --domain ai --output telegram
