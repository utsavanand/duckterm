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
elif "ORIGINAL RECORDS WITH ATTRIBUTION AND SOURCE REFERENCES:" in prompt:
    records = json.loads(prompt.split("ORIGINAL RECORDS WITH ATTRIBUTION AND SOURCE REFERENCES:")[1].split("CURRENT CONTEXT AND SESSION/FOLDER/HARNESS IDENTITIES:")[0].strip())
    ref = records[0]["ref"]
    items = [("Search", "Use rg, not grep"), ("Commit style", "No emoji in commit messages")]
    if records[0]["kind"] == "manual":
        draft = json.loads(records[0]["text"])
        items = [(draft["title"], draft["text"])]
    print(json.dumps({"decisions": [{"claim": text, "memory_kind": "procedural", "source_refs": [ref], "action": "create_rule", "reason": "A distinct reusable instruction.", "target": None, "changes": {"rule": {"title": title, "text": text, "scope": "all"}}, "context": None} for title, text in items]}))
else:
    print("- [all] [1] Use rg, not grep\n- [all] [1] No emoji in commit messages")
