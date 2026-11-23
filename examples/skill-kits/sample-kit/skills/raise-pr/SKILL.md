---
name: raise-pr
description: Raises a pull request for the current branch with a problem / fix / how-tested body. Use when the work is committed and ready for review.
---
# Raise a pull request

1. Confirm the branch is pushed and the tree is clean (`git status`).
2. Write the body in three short paragraphs: the problem, the fix, how it was tested.
3. Open the PR with `gh pr create --fill-first`, then replace the body with yours.
4. Report the PR URL. Do not merge.
