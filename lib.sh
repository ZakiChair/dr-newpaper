#!/usr/bin/env bash
# Shared by daily_digest.sh and weekly_digest.sh. Sourced, not executed.

# Read .env into the environment.
#
# Not `export $(cat .env | grep -v '^#' | xargs)`: xargs splits on whitespace, so
# `DR_NEWPAPER_MODEL=MiniMax M3 Pro` exported just "MiniMax" and the cron run then
# talked to a model that does not exist. Not `. ./.env` either — sourcing would
# execute the rest of such a line as a command, and a value like $(...) would run.
#
# Parsing matches the repo's Python loaders (bot.py, pdf_sender.py): split on the
# first '=' only, strip whitespace around both halves, skip blanks and comments.
# Two readers of one file must not disagree about what it says.
load_env() {
  local file="${1:-.env}" line key value
  if [ ! -f "$file" ]; then
    echo "Erreur : $file introuvable dans $(pwd)" >&2
    return 1
  fi
  # `|| [ -n "$line" ]` so a final line without a trailing newline is still read.
  while IFS= read -r line || [ -n "$line" ]; do
    # Trim leading whitespace, then skip blanks and comments.
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in
      ''|'#'*) continue ;;
      *'='*)   ;;
      *)       continue ;;   # no '=': not an assignment
    esac
    key="${line%%=*}"
    value="${line#*=}"
    key="${key//[[:space:]]/}"                      # names never contain spaces
    value="${value#"${value%%[![:space:]]*}"}"      # ltrim
    value="${value%"${value##*[![:space:]]}"}"      # rtrim
    [ -n "$key" ] || continue
    export "$key=$value"
  done < "$file"
}
