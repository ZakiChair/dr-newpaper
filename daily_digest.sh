#!/usr/bin/env bash
# Daily AI research digest — dispatch to Telegram
set -e

# Run from the repo this script lives in, whatever cron's working directory is.
cd "$(dirname "$0")"
# Checked explicitly: set -e does not catch this. A failing `cat` inside the
# substitution leaves `export` with no operand, which succeeds (and prints the
# whole environment), so cron would silently run the digest with no credentials.
# Before this script cd'd here, the cd itself failed and stopped the run.
[ -f .env ] || { echo "Erreur : .env introuvable dans $(pwd)" >&2; exit 1; }
export $(cat .env | grep -v '^#' | xargs)

python3 run.py "artificial intelligence machine learning" --max 10 --domain ai --output telegram
