#!/bin/sh
# At stop time, add a reminder to run the focused tests if the workspace changed.
if git -C "${CLAUDE_PROJECT_DIR:-.}" diff --quiet 2>/dev/null; then exit 0; fi
printf '{"decision": "allow", "context": "sample-kit: the workspace has uncommitted changes; run the focused tests before handing off."}\n'
exit 0
