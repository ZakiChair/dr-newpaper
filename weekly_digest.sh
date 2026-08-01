#!/usr/bin/env bash
# Weekly science digest — physics, maths, biology — most popular papers
set -e

# Walk to the real file before locating the repo: a cron job is normally
# installed as a symlink into a directory on PATH, and dirname of the link would
# land there instead. readlink -f would do this in one step but does not exist on
# macOS. Duplicated in the sibling digest script because this is what finds
# lib.sh in the first place, so it cannot itself live in lib.sh.
script=$0
while [ -L "$script" ]; do
  link=$(readlink "$script")
  case $link in
    /*) script=$link ;;
    *)  script=$(dirname "$script")/$link ;;
  esac
done
cd "$(dirname "$script")"
. ./lib.sh
load_env

echo "📊 Weekly Physics Digest"
python3 run.py "physics quantum field theory gravitation" --max 5 --domain physics --output both

echo "📊 Weekly Maths Digest"
python3 run.py "mathematics topology algebra geometry" --max 5 --domain mathematic --output both

echo "📊 Weekly Biology Digest"
python3 run.py "biology genomics CRISPR neuroscience" --max 5 --domain biology --output both
