#!/usr/bin/env bash
# Daily AI research digest — dispatch to Telegram
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

python3 run.py "artificial intelligence machine learning" --max 10 --domain ai --output telegram
