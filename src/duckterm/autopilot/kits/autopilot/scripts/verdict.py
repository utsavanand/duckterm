#!/usr/bin/env python3
"""Flow exec step: combine the reviewers' verdicts into one routing choice.
Reads the step's inputs as JSON on stdin (one verdict per reviewer, named as
the flow names them), prints one JSON line. No model.

ship when every reviewer approved or passed; stop when any rejected; revise
otherwise, a missing verdict included."""

import json
import sys

inputs = json.load(sys.stdin)
verdicts = {str(k): v for k, v in inputs.items()}
if any(v == "reject" for v in verdicts.values()):
    choice = "stop"
elif verdicts and all(v in ("approve", "pass") for v in verdicts.values()):
    choice = "ship"
else:
    choice = "revise"
reason = ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())) or "no verdicts"
print(json.dumps({"choice": choice, "reason": reason, "outputs": verdicts}))
