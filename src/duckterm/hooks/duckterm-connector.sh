#!/bin/bash
# Stable v1 MCP dispatcher. Runtime records are fixed fields, never shell code.
set -eu
fail() { printf '%s\n' "Duckterm connector: $1" >&2; exit 1; }
[ "$#" = 2 ] && [ "$1" = connector-run ] || fail 'invalid invocation'
case "$2" in github|railway|porkbun) ;; *) fail 'unknown connector' ;; esac
ROOT="${BASH_SOURCE[0]%/*}"
W_HOME="${DUCKTERM_HOME:-}"
W_INSTANCE="${DUCKTERM_INSTANCE:-}"
W_PROFILE="${DUCKTERM_DESKTOP_PROFILE:-}"
W_PORT="${DUCKTERM_PORT:-}"
W_SOCKET="${DUCKTERM_TMUX_SOCKET:-}"
W_URL="${DUCKTERM_URL:-}"
if [ -z "$W_HOME$W_INSTANCE$W_PROFILE$W_PORT$W_SOCKET$W_URL" ]; then
  [ -z "${DUCKTERM_SESSION_KEY:-}${DUCKTERM_SESSION_TOKEN_FILE:-}" ] \
    || fail 'session workspace context is missing; reopen it in its owning app'
  W_HOME="${HOME:?}/.duckterm"
  W_PORT=4300
  W_SOCKET=duckterm
  W_URL=http://127.0.0.1:4300
elif [ -z "$W_HOME$W_INSTANCE$W_PROFILE" ] && [ "$W_URL" = http://127.0.0.1:4300 ] \
  && [ "${W_PORT:-4300}" = 4300 ] && [ "${W_SOCKET:-duckterm}" = duckterm ]; then
  # Old running Full panes received only this URL.
  W_HOME="${HOME:?}/.duckterm"
  W_PORT=4300
  W_SOCKET=duckterm
else
  [ -n "$W_HOME" ] && [ -n "$W_PORT" ] && [ -n "$W_SOCKET" ] && [ -n "$W_URL" ] \
    || fail 'incomplete workspace context; reopen the session in its owning app'
fi
# HOME can have a trusted symlink prefix (for example /tmp on macOS).
if [ -d "$W_HOME" ]; then W_HOME=$(CDPATH= cd -- "$W_HOME" && pwd -P); fi
MATCH=''
MATCH_PROFILE=''
MATCH_PATH=''
for RECORD in "$ROOT"/runtimes/*.target; do
  [ -f "$RECORD" ] && [ ! -L "$RECORD" ] || continue
  {
    IFS= read -r FORMAT && IFS= read -r R_HOME && IFS= read -r R_INSTANCE \
      && IFS= read -r R_PROFILE && IFS= read -r R_PORT && IFS= read -r R_SOCKET \
      && IFS= read -r R_URL && IFS= read -r R_COMMAND && IFS= read -r R_PATH || continue
    if IFS= read -r EXTRA; then continue; fi
  } < "$RECORD"
  [ "$FORMAT" = duckterm-connector-target-v1 ] || continue
  [ "$W_HOME" = "$R_HOME" ] && [ "$W_INSTANCE" = "$R_INSTANCE" ] \
    && [ "$W_PORT" = "$R_PORT" ] && [ "$W_SOCKET" = "$R_SOCKET" ] \
    && [ "$W_URL" = "$R_URL" ] || continue
  [ -z "$W_PROFILE" ] || [ "$W_PROFILE" = "$R_PROFILE" ] || continue
  [ -z "$MATCH" ] || fail 'ambiguous workspace runtime; reopen its app'
  MATCH="$R_COMMAND"
  MATCH_PROFILE="$R_PROFILE"
  MATCH_PATH="$R_PATH"
done
[ -n "$MATCH" ] || fail 'workspace runtime is not registered; open its owning app'
case "$MATCH" in /*) ;; *) fail 'workspace runtime is not an absolute path' ;; esac
[ -f "$MATCH" ] && [ -x "$MATCH" ] || fail 'owning app or CLI is unavailable; open it after moving or reinstalling'
# A connector is already profile-bound. A stale inherited session enrollment or
# another app's default must not override that target in the CLI bootstrap.
unset DUCKTERM_SESSION_KEY DUCKTERM_SESSION_TOKEN_FILE DUCKTERM_APP_PROFILE DUCKTERM_EDITION
export DUCKTERM_HOME="$W_HOME" DUCKTERM_INSTANCE="$W_INSTANCE" DUCKTERM_DESKTOP_PROFILE="$MATCH_PROFILE"
export DUCKTERM_PORT="$W_PORT" DUCKTERM_TMUX_SOCKET="$W_SOCKET" DUCKTERM_URL="$W_URL"
# The executable itself is always absolute. Its provider tools use the owning
# runtime's recorded search path, with basic OS tools available to app wrappers.
export PATH="$MATCH_PATH:/usr/bin:/bin:/usr/sbin:/sbin"
exec "$MATCH" "$@"
