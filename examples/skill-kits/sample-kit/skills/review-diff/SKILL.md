---
name: review-diff
description: Reviews a diff for correctness and returns a verdict with findings. Use before merging or when asked for a code review.
---
# Review a diff

Read the diff you were given. Look for behaviour changes without tests, error
paths that swallow failures, and anything that runs at install or import time.

Write the findings to `docs/reviews/findings.md`, one bullet each with file and
line. Then answer with exactly one of these choices and a one-line reason:

- `approve` — nothing blocks a merge
- `revise` — fixable issues, listed in the findings
- `reject` — the change should not land in this form
