---
name: build-fix
description: Makes the change a spec describes, with a test. Use when a flow asks to build the fix.
---
# Build the fix

Read the spec from the previous step (`spec.md` in your inputs). Make the
change it describes, and add or extend the test it names. Keep the change to
what the spec says; do not refactor nearby code. Run the tests you touched
and fix what fails. Do not commit: the flow commits for you.
