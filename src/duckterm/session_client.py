"""Commands an enrolled session can use on demand, without an owner credential."""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from duckterm.helpers.session_credentials import client_credentials
from duckterm.persistence.artifacts import registration


def add_parser(sub: Any) -> None:
    parser = sub.add_parser(
        "session", help="read your session card, discover peers, and answer inbox questions"
    )
    actions = parser.add_subparsers(dest="session_action", required=True)
    actions.add_parser("self", help="show your live session card")
    discover = actions.add_parser("discover", help="discover permitted sessions")
    discover.add_argument(
        "--scope",
        choices=["self_folder", "parent", "grandparent", "shared_root"],
        default="shared_root",
    )
    discover.add_argument("--cursor")
    inbox = actions.add_parser("inbox", help="read pending questions when you choose to respond")
    inbox.add_argument("--before", type=int)
    ask = actions.add_parser("ask", help="send a question; returns an ID to check later")
    ask.add_argument("target")
    ask.add_argument("question")
    ask.add_argument(
        "--work-title", help="mark an implementation assignment; acceptance creates tracked work"
    )
    ask.add_argument(
        "--parent-request", help="request being relayed; links status updates back to its sender"
    )
    ask.add_argument(
        "--request-key", default=None, help="reuse this key when retrying the same question"
    )
    ask.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="optional deadline in seconds; 0 (default) keeps the request pending",
    )
    for action in ("get", "accept", "reply", "decline", "cancel"):
        child = actions.add_parser(action)
        child.add_argument("request_id")
        if action == "accept":
            child.add_argument(
                "--work-title", help="track this assignment separately from the reply"
            )
        if action in ("reply", "decline"):
            child.add_argument("--file", default="-", help="UTF-8 answer file; default reads stdin")
    work = actions.add_parser("work", help="track assigned outcomes independently of inbox replies")
    work_actions = work.add_subparsers(dest="work_action", required=True)
    work_list = work_actions.add_parser("list")
    work_list.add_argument("--before", type=int)
    work_get = work_actions.add_parser("get")
    work_get.add_argument("work_id")
    work_create = work_actions.add_parser("create")
    work_create.add_argument("title")
    work_create.add_argument("--request", required=True)
    work_update = work_actions.add_parser("update")
    work_update.add_argument("work_id")
    work_update.add_argument(
        "--state", choices=["proposed", "accepted", "in_progress", "blocked", "done", "dropped"]
    )
    work_update.add_argument("--note")
    work_update.add_argument("--blocker")
    work_update.add_argument("--evidence")
    work_update.add_argument("--assign", dest="owner_session")
    artifacts = actions.add_parser("artifacts", help="list this session's saved artifacts")
    artifacts.set_defaults(session_action="artifacts")
    artifact = actions.add_parser("artifact", help="register a generated file in the Mac app")
    artifact.add_argument("file", type=Path)
    artifact.add_argument("--title")
    publish = actions.add_parser("publish", help="update your purpose and current activity")
    publish.add_argument("--purpose")
    publish.add_argument("--activity")


def main(args: argparse.Namespace) -> int:
    try:
        url, token = client_credentials()
        method = "GET"
        body: dict[str, Any] | None = None
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        action = args.session_action
        if action == "work":
            path = "/work"
            if args.work_action == "list":
                if args.before is not None:
                    path += f"?before={args.before}"
            elif args.work_action == "create":
                method = "POST"
                body = {"title": args.title, "origin_request_id": args.request}
            else:
                path += "/" + urllib.parse.quote(args.work_id, safe="")
                if args.work_action == "update":
                    method = "PATCH"
                    body = {
                        field: getattr(args, field)
                        for field in ("state", "note", "blocker", "evidence", "owner_session")
                        if getattr(args, field) is not None
                    }
        elif action == "self":
            path = "/self"
        elif action == "artifacts":
            path = "/artifacts"
        elif action == "artifact":
            method, path = "POST", "/artifacts"
            body = registration(args.file, args.title)
        elif action == "discover":
            query = {"scope": args.scope}
            if args.cursor:
                query["cursor"] = args.cursor
            path = "/peers?" + urllib.parse.urlencode(query)
        elif action == "inbox":
            path = "/inbox" + (f"?before={args.before}" if args.before is not None else "")
        elif action == "ask":
            method, path = "POST", "/questions"
            headers["Idempotency-Key"] = args.request_key or str(uuid.uuid4())
            body = {
                "target_session_id": args.target,
                "question": args.question,
                "timeout_seconds": args.timeout,
            }
            if args.work_title:
                body["work_title"] = args.work_title
            if args.parent_request:
                body["parent_request_id"] = args.parent_request
        elif action == "publish":
            method, path = "PATCH", "/self"
            body = {
                field: getattr(args, field)
                for field in ("purpose", "activity")
                if getattr(args, field) is not None
            }
        else:
            path = "/questions/" + urllib.parse.quote(args.request_id, safe="")
            if action != "get":
                method = "POST"
                path += "/" + ("answer" if action == "reply" else action)
                body = {}
                if action == "accept" and args.work_title:
                    body["work_title"] = args.work_title
                if action in ("reply", "decline"):
                    text = (
                        sys.stdin.read()
                        if args.file == "-"
                        else Path(args.file).read_text(encoding="utf-8")
                    )
                    body = {"text": text}
        request = urllib.request.Request(
            url + "/api/v1/session" + path,
            method=method,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
        if action == "inbox":
            result["instructions"] = (
                "Handle pending inbox work before starting unrelated work, "
                "within your existing authorization. "
                "Owner broadcasts are labeled sender_kind=owner and require no reply; "
                "reading them marks them read. For peer questions, "
                "use duckterm session accept REQUEST_ID, then "
                "duckterm session reply REQUEST_ID --file answer.txt. Read the next page "
                "with --before next_cursor. For implementation assignments, use accept "
                "--work-title TITLE; replies do not close tracked work. Check the work "
                "and work_updates fields, and use duckterm session work list/update "
                "to record progress, blockers or completion evidence. Closed messages "
                "may still have open work items."
            )
        if action in {"self", "inbox"}:
            result["artifact_instructions"] = (
                "Automatically register user-facing files you generate with "
                "duckterm session artifact /absolute/path --title 'Useful title'. "
                "Saved copies appear in the Mac app Artifacts tab. Local-only, "
                "5 MiB/file; register deliverables, not source edits, logs or credentials."
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except urllib.error.HTTPError as exc:
        print(f"Session API {exc.code}: {exc.read().decode(errors='replace')}", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    return 1
