---
name: check-fix
description: Checks a built fix against its spec and answers ok or broken. Use when a flow asks to check the fix.
---
# Check the fix

Compare the diff in your inputs with the spec. Run the tests
the spec names. Answer `ok` when the change does what the spec says and the
tests pass; otherwise answer `broken` and say why in one paragraph. Do not
change any file.
