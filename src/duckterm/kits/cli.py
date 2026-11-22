"""`duckterm kit validate | plan | install | uninstall | verify | run | runs`. Works from a
bare clone with no server; the dashboard is a front end to the same calls."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from duckterm.kits import install as inst
from duckterm.kits import manifest as m
from duckterm.kits import plan as planning
from duckterm.kits import runner


def add_parser(sub: Any) -> None:
    parser = sub.add_parser("kit", help="validate, plan, install, uninstall or verify a skill kit")
    actions = parser.add_subparsers(dest="kit_action", required=True)

    def common(p: argparse.ArgumentParser, *, kit: bool = True) -> None:
        if kit:
            p.add_argument(
                "kit", help="path to the kit folder (holds skill-kit.toml), or to a manifest file"
            )
        p.add_argument("--project", default=".", help="repository to install into (default: cwd)")
        p.add_argument(
            "--harness",
            action="append",
            default=None,
            help="claude-code | codex | copilot | opencode; repeatable; default: the ones on PATH",
        )
        p.add_argument("--option", action="append", default=[], metavar="NAME=CHOICE")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    common(actions.add_parser("validate", help="authoring rules; runs no kit code"), kit=True)
    common(
        actions.add_parser("plan", help="every file and config edit, per harness, without writing")
    )
    install = actions.add_parser("install", help="write the kit into the project and record it")
    common(install)
    install.add_argument(
        "--replace", action="store_true", help="overwrite files that exist and are not from a kit"
    )
    uninstall = actions.add_parser(
        "uninstall", help="remove exactly what the record says this kit wrote"
    )
    uninstall.add_argument("name")
    uninstall.add_argument("--project", default=".")
    uninstall.add_argument("--json", action="store_true")
    verify = actions.add_parser(
        "verify", help="check every recorded file against its hash; exit 1 on drift"
    )
    verify.add_argument("--project", default=".")
    verify.add_argument("--json", action="store_true")
    run = actions.add_parser(
        "run", help="run an installed flow (<kit>/<flow>); exit 3 while it waits at a gate"
    )
    run.add_argument("flow", nargs="?", help="<kit>/<flow>, or a path to a flow .json")
    run.add_argument("--project", default=".")
    run.add_argument("--harness", default=None, help="claude-code | codex (default: first on PATH)")
    run.add_argument("--input", action="append", default=[], metavar="NAME=VALUE")
    run.add_argument("--resume", metavar="RUN_ID", help="continue a paused or interrupted run")
    run.add_argument(
        "--gate", nargs=2, metavar=("STEP", "ANSWER"), help="answer the gate the run waits at"
    )
    run.add_argument("--yolo", action="store_true", help="skip harness permission prompts")
    run.add_argument(
        "--codex-trusted", action="store_true", help="this folder's Codex hooks are trusted"
    )
    run.add_argument("--step-limit", type=int, default=None)
    run.add_argument("--json", action="store_true")
    runs = actions.add_parser("runs", help="list the runs recorded in this project")
    runs.add_argument("--project", default=".")
    runs.add_argument("--json", action="store_true")


def _options(pairs: list[str]) -> dict[str, str]:
    out = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--option needs NAME=CHOICE, got {pair!r}")
        out[name] = value
    return out


def _plan(args: argparse.Namespace) -> planning.Plan:
    kit = m.load(Path(args.kit))
    harnesses = args.harness or planning.detect_harnesses()
    if not harnesses:
        raise SystemExit("no harness found on PATH; pass --harness")
    return planning.build(kit, Path(args.project), harnesses, _options(args.option))


def _print_plan(plan: planning.Plan, *, files: bool) -> None:
    kit = plan.kit
    print(f"{kit.name} {kit.version} → {plan.project}  [{', '.join(plan.harnesses)}]")
    if plan.options:
        print("options: " + ", ".join(f"{k}={v}" for k, v in plan.options.items()))
    for f in plan.findings:
        print(f"  {f}")
    if plan.conflicts:
        print("conflicts:")
        for c in plan.conflicts:
            print(f"  {c.path or c.component}: {c.detail}  [{' / '.join(c.choices)}]")
    width = max((len(r["component"]) for r in plan.rows()), default=10)
    print()
    print("  " + "component".ljust(width) + "  " + "  ".join(h.ljust(22) for h in plan.harnesses))
    for row in plan.rows():
        cells = [row["harnesses"].get(h, "—").ljust(22) for h in plan.harnesses]
        print("  " + row["component"].ljust(width) + "  " + "  ".join(cells))
        for note in row["notes"]:
            print("  " + " " * width + "    · " + note)
    if files:
        print()
        print("files:")
        for p in plan.files():
            print(f"  {p.op:11} {p.path}" + (f"  ({p.harness})" if p.harness != "shared" else ""))


def main(args: argparse.Namespace) -> int:
    try:
        action = args.kit_action
        if action == "validate":
            kit = m.load(Path(args.kit))
            from duckterm.kits import validate as v

            findings = v.validate(kit)
            if args.json:
                print(json.dumps([f.__dict__ for f in findings], indent=2))
            else:
                print(f"{kit.name} {kit.version}: {len(kit.components)} components")
                for f in findings:
                    print(f"  {f}")
                if not findings:
                    print("  ok")
            return 1 if any(f.level == "error" for f in findings) else 0
        if action == "plan":
            plan = _plan(args)
            if args.json:
                print(json.dumps(plan.to_json(), indent=2))
            else:
                _print_plan(plan, files=True)
            return 1 if plan.errors or plan.conflicts else 0
        if action == "install":
            plan = _plan(args)
            report = inst.apply(plan, replace=args.replace)
            if args.json:
                print(json.dumps({"plan": plan.to_json(), "report": report.__dict__}, indent=2))
            else:
                _print_plan(plan, files=False)
                print()
                print(f"wrote {len(report.written)} entries; record at {planning.RECORD}")
                for note in report.notes:
                    print("  " + note)
            return 0
        if action == "uninstall":
            report = inst.uninstall(Path(args.project), args.name)
            if args.json:
                print(json.dumps(report.__dict__, indent=2))
            else:
                for path in report.removed:
                    print(f"  removed  {path}")
                for path in report.kept:
                    print(f"  kept     {path}  (edited since install)")
                for note in report.notes:
                    print("  " + note)
            return 0
        if action == "run":
            return _run(args)
        if action == "runs":
            rows = runner.list_runs(Path(args.project))
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                if not rows:
                    print("no runs here")
                for row in rows:
                    where = row["current"]["step"] if row.get("current") else "—"
                    print(f"  {row['run_id']}  {row['flow']:<28} {row['state']:<13} at {where}")
            return 0
        if action == "verify":
            reports = inst.verify(Path(args.project))
            if args.json:
                print(json.dumps([r.__dict__ for r in reports], indent=2))
            else:
                if not reports:
                    print("no kits recorded here")
                for r in reports:
                    state = "ok" if r.ok() else "drift"
                    print(f"{r.kit}: {state}")
                    for path in r.missing:
                        print(f"  missing  {path}")
                    for path in r.drifted:
                        print(f"  drifted  {path}")
            return 0 if all(r.ok() for r in reports) else 1
    except runner.GatePending as pending:
        _print_gate(pending.run, pending.gate)
        return 3
    except (m.ManifestError, inst.InstallRefused, ValueError, OSError) as exc:
        print(f"duckterm kit: {exc}", file=sys.stderr)
        return 1
    return 2


def _server_run(args: argparse.Namespace) -> int | None:
    """Inside a DuckTerm session, a run goes through the server so each step
    shows as a child session; the terminal watches the run file through the
    session API and prints what it sees. Returns None when there is no
    session to speak for, so the local runner takes over."""
    import os
    import time
    import urllib.error
    import urllib.request

    from duckterm.helpers import session_credentials

    if not os.environ.get("DUCKTERM_SESSION_KEY"):
        return None
    try:
        url, token = session_credentials.client_credentials()
    except ValueError:
        return None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def call(route: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{url}/api/v1/session/kits/{route}",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data if isinstance(data, dict) else {}

    project = str(Path(args.project).resolve())
    try:
        if args.resume:
            if args.gate:
                call(
                    "gate",
                    {
                        "project": project,
                        "run_id": args.resume,
                        "step": args.gate[0],
                        "answer": args.gate[1],
                    },
                )
            run_id = args.resume
        else:
            if not args.flow:
                raise SystemExit("duckterm kit run: give <kit>/<flow> or --resume RUN_ID")
            started = call(
                "run",
                {
                    "project": project,
                    "flow": args.flow,
                    "harness": args.harness,
                    "inputs": _options(args.input),
                },
            )
            run_id = str(started["run_id"])
            print(f"{run_id}  {args.flow}  started through the DuckTerm server")
            print("  each step runs as a child session of this one")
        seen = 0
        while True:
            detail = call("run-detail", {"project": project, "run_id": run_id})
            state = detail["run"]
            for a in state["attempts"][seen:]:
                if a["status"] != "started":
                    choice = f"  → {a['choice']}" if a.get("choice") else ""
                    head = f"  {a['step']:<14} attempt {a['attempt']}  {a['kind']:<9} "
                    print(head + a["status"] + choice)
            seen = len([a for a in state["attempts"] if a["status"] != "started"])
            if state["state"] in ("succeeded", "failed", "waiting_gate", "cancelled"):
                break
            time.sleep(2)
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode()).get("error", str(exc))
        except ValueError:
            message = str(exc)
        print(f"duckterm kit: {message}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"duckterm kit: {exc}", file=sys.stderr)
        return 1
    if state["state"] == "waiting_gate":
        gate = [g for g in state["gates"] if g["response"] is None][-1]
        print(
            f"gate {gate['step']!r} (visit {gate['visit']}): {gate.get('question') or 'approve?'}"
        )
        print(
            f"  answer with: duckterm kit run --resume {run_id} --gate {gate['step']} "
            + "|".join(gate["choices"])
        )
        return 3
    if state.get("failure"):
        f = state["failure"]
        print(f"failed at {f['step']} (attempt {f['attempt']}): {f['reason']}")
    else:
        print(f"succeeded; run file {runner.RUNS}/{run_id}/run.json")
    return 0 if state["state"] == "succeeded" else 1


def _run(args: argparse.Namespace) -> int:
    served = _server_run(args)
    if served is not None:
        return served
    project = Path(args.project)
    limits = runner.Limits(yolo=args.yolo, codex_trusted=args.codex_trusted)
    if args.step_limit:
        limits.steps = args.step_limit
    if args.resume:
        gate = (args.gate[0], args.gate[1]) if args.gate else None
        run = runner.resume(project, args.resume, gate=gate, limits=limits)
    else:
        if not args.flow:
            raise SystemExit("duckterm kit run: give <kit>/<flow> or --resume RUN_ID")
        harness = args.harness or next(
            (h for h in planning.detect_harnesses() if h in runner.RUNNER_HARNESSES), None
        )
        if not harness:
            raise SystemExit("no supported harness on PATH (claude-code, codex); pass --harness")
        session = os.environ.get("DUCKTERM_SESSION_KEY", "")
        run = runner.start(
            project,
            args.flow,
            harness=harness,
            inputs=_options(args.input),
            limits=limits,
            executor_state={"session": session} if session else None,
        )
    if args.json:
        print(json.dumps(run.state, indent=2, sort_keys=True, default=str))
    else:
        _print_run(run)
    state = run.state["state"]
    if state == "waiting_gate":
        return 3
    return 0 if state == "succeeded" else 1


def _print_run(run: runner.Run) -> None:
    s = run.state
    print(f"{s['run_id']}  {s['flow']}  [{s['harness']}, {s['executor']}]  {s['state']}")
    for a in s["attempts"]:
        choice = f"  → {a['choice']}" if a.get("choice") else ""
        reason = f"  ({a['reason']})" if a.get("reason") and a["status"] != "completed" else ""
        head = f"  {a['step']:<14} attempt {a['attempt']}  {a['kind']:<9} {a['status']:<10}"
        print(head + choice + reason)
    if s["state"] == "waiting_gate":
        gate = [g for g in s["gates"] if g["response"] is None][-1]
        _print_gate(run, gate)
    if s.get("failure"):
        f = s["failure"]
        print(f"failed at {f['step']} (attempt {f['attempt']}): {f['reason']}")
    if s["state"] == "succeeded":
        print(f"succeeded; run file {runner.RUNS}/{s['run_id']}/run.json")


def _print_gate(run: runner.Run, gate: dict[str, Any]) -> None:
    print(f"gate {gate['step']!r} (visit {gate['visit']}): {gate.get('question') or 'approve?'}")
    if gate.get("show"):
        shown = gate["show"]
        where = shown.get("path") if isinstance(shown, dict) else shown
        print(f"  shows: {where}")
    print(
        f"  answer with: duckterm kit run --resume {run.id} --gate {gate['step']} "
        + "|".join(gate["choices"])
    )
