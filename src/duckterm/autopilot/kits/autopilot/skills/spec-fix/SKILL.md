---
name: spec-fix
description: Writes a short spec for fixing an issue in this repository. Use when a flow asks for the fix's spec.
---
# Spec the fix

The issue is in your inputs. Read the code it concerns, then write `spec.md`
in your output directory with these sections:

- **Problem:** what is wrong, in the issue reporter's terms, and where in the
  code it happens (file and function).
- **Fix:** the change you would make, file by file.
- **Test:** how to show the fix works: an existing test to extend, or the
  new test to add.
- **Risk:** what else the change could break.

If the issue does not describe a problem you can find in the code, say so
under Problem and stop. Do not change any file in the repository.
