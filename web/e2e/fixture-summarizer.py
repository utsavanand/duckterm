"""Deterministic model replies; browser checks still exercise real inbox delivery."""

import json
import sys

prompt = sys.stdin.read()
if prompt.startswith("Classify the latest owner request in a DuckTerm folder chat."):
    data = json.loads(prompt.rsplit("\n", 1)[-1])
    if data["owner_request"] == "Ask ui-dev to report progress.":
        target = next(t for t in data["targets"] if t["kind"] == "session" and t["name"] == "ui-dev")
        response = {"action": "message", "target": {"kind": target["kind"], "id": target["id"]}, "text": "Report your progress."}
    elif data["owner_request"] == "Tell everyone in this folder to report progress.":
        response = {"action": "message", "target": {"kind": "folder", "id": data["folder"]}, "text": "Report your progress."}
    else:
        response = {"action": "answer"}
    print(json.dumps(response))
else:
    print("- [all] [1] Use rg, not grep\n- [all] [1] No emoji in commit messages")
