#!/bin/sh
# Blocks destructive shell commands. Reads the neutral skill-kit payload on stdin.
payload=$(cat)
command=$(printf '%s' "$payload" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("tool") or {}).get("input", {}).get("command", ""))' 2>/dev/null)
case "$command" in
  *"rm -rf /"*|*"git push --force"*|*"git push -f"*|*"git reset --hard"*)
    printf '{"decision": "block", "reason": "sample-kit: destructive command refused: %s"}\n' "$command"
    exit 2 ;;
esac
exit 0
