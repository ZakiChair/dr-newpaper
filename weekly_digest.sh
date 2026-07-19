#!/usr/bin/env bash
# Weekly science digest — physics, maths, biology — most popular papers
set -e

cd /tmp/hermy_repo/Projects/recherche
export $(cat .env | grep -v '^#' | xargs)

echo "📊 Weekly Physics Digest"
python3 run.py "physics quantum field theory gravitation" --max 5 --domain physics --output both

echo "📊 Weekly Maths Digest"
python3 run.py "mathematics topology algebra geometry" --max 5 --domain mathematic --output both

echo "📊 Weekly Biology Digest"
python3 run.py "biology genomics CRISPR neuroscience" --max 5 --domain biology --output both
