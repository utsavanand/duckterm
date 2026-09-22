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
        if action in ("reply", "decline"):
            child.add_argument("--file", default="-", help="UTF-8 answer file; default reads stdin")
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
        if action == "self":
            path = "/self"
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
                "Use duckterm session accept REQUEST_ID, then "
                "duckterm session reply REQUEST_ID --file answer.txt. Read the next page "
                "with --before next_cursor. Answered/expired/cancelled requests need no action."
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except urllib.error.HTTPError as exc:
        print(f"Session API {exc.code}: {exc.read().decode(errors='replace')}", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    return 1
