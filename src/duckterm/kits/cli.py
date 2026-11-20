"""`duckterm kit validate | plan | install | uninstall | verify`. Works from a
bare clone with no server; the dashboard is a front end to the same calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from duckterm.kits import install as inst
from duckterm.kits import manifest as m
from duckterm.kits import plan as planning


def add_parser(sub: Any) -> None:
    parser = sub.add_parser("kit", help="validate, plan, install, uninstall or verify a skill kit")
    actions = parser.add_subparsers(dest="kit_action", required=True)

    def common(p: argparse.ArgumentParser, *, kit: bool = True) -> None:
        if kit:
            p.add_argument("kit", help="path to the kit folder (holds skill-kit.toml)")
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
    except (m.ManifestError, inst.InstallRefused, ValueError, OSError) as exc:
        print(f"duckterm kit: {exc}", file=sys.stderr)
        return 1
    return 2
