#!/usr/bin/env bash
# Shared by daily_digest.sh and weekly_digest.sh. Sourced, not executed.

# Read .env into the environment.
#
# Not `export $(cat .env | grep -v '^#' | xargs)`: xargs splits on whitespace, so
# `DR_NEWPAPER_MODEL=MiniMax M3 Pro` exported just "MiniMax" and the cron run then
# talked to a model that does not exist. Not `. ./.env` either — sourcing would
# execute the rest of such a line as a command, and a value like $(...) would run.
#
# Parsing matches config.parse_env_line, which is the definition of the format:
# split on the first '=' only, strip whitespace around both halves, then one
# matching pair of surrounding quotes, skipping blanks and comments. Two readers
# of one file must not disagree about what it says.
load_env() {
  # The locals are prefixed because `export` applied to a name declared `local`
  # marks the *local* as exported, and it vanishes when the function returns: a
  # .env key named like one of these would be read, exported and silently lost.
  local _le_file="${1:-.env}" _le_line _le_key _le_value

  if [ ! -f "$_le_file" ]; then
    echo "Erreur : $_le_file introuvable dans $(pwd)" >&2
    return 1
  fi

  # `|| [ -n "$_le_line" ]` so a final line without a trailing newline is read.
  while IFS= read -r _le_line || [ -n "$_le_line" ]; do
    # Trim leading whitespace, then skip blanks and whole-line comments.
    _le_line="${_le_line#"${_le_line%%[![:space:]]*}"}"
    case "$_le_line" in
      ''|'#'*) continue ;;
      *'='*)   ;;
      *)       continue ;;   # no '=': not an assignment
    esac

    _le_key="${_le_line%%=*}"
    _le_value="${_le_line#*=}"
    _le_key="${_le_key//[[:space:]]/}"                      # names hold no spaces
    _le_value="${_le_value#"${_le_value%%[![:space:]]*}"}"  # ltrim
    _le_value="${_le_value%"${_le_value##*[![:space:]]}"}"  # rtrim
    # One matching pair of surrounding quotes, as config.parse_env_line does.
    case "$_le_value" in
      \"?*\") _le_value="${_le_value#\"}"; _le_value="${_le_value%\"}" ;;
      \'?*\') _le_value="${_le_value#\'}"; _le_value="${_le_value%\'}" ;;
    esac
    [ -n "$_le_key" ] || continue

    # An already-exported value wins, as config.load_env_file does: someone who
    # ran `TELEGRAM_CHAT_ID=999 ./daily_digest.sh` meant it. printenv rather than
    # ${!name+set}, which also sees shell variables that were never exported.
    if ! printenv "$_le_key" >/dev/null 2>&1; then
      export "$_le_key=$_le_value"
    fi
  done < "$_le_file"
}
