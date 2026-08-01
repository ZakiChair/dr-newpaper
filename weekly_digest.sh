#!/usr/bin/env bash
# Weekly science digest — physics, maths, biology — most popular papers
set -e

# Run from the repo this script lives in, whatever cron's working directory is.
cd "$(dirname "$0")"
# See daily_digest.sh: set -e does not stop a missing .env here.
[ -f .env ] || { echo "Erreur : .env introuvable dans $(pwd)" >&2; exit 1; }
export $(cat .env | grep -v '^#' | xargs)

echo "📊 Weekly Physics Digest"
python3 run.py "physics quantum field theory gravitation" --max 5 --domain physics --output both

echo "📊 Weekly Maths Digest"
python3 run.py "mathematics topology algebra geometry" --max 5 --domain mathematic --output both

echo "📊 Weekly Biology Digest"
python3 run.py "biology genomics CRISPR neuroscience" --max 5 --domain biology --output both
