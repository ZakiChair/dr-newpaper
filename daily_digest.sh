#!/usr/bin/env bash
# Daily AI research digest — dispatch to Telegram
set -e

# Walk to the real file before locating the repo: a cron job is normally
# installed as a symlink (ln -s ~/dr-newpaper/daily_digest.sh /usr/local/bin/),
# and dirname of the link would land in /usr/local/bin. readlink -f would do
# this in one step but does not exist on macOS. Duplicated in weekly_digest.sh
# because this is what finds lib.sh in the first place.
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
