"""Apply a plan, write the record, uninstall from the record, verify (§5, §9,
§III.4, §III.12). Every write is temp-file + rename. The record is the sole
authority for uninstall: nothing DuckTerm did not write is ever removed, and
an edited file is kept and listed."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from duckterm.core import agents_rules
from duckterm.kits import adapters
from duckterm.kits import manifest as m
from duckterm.kits.plan import RECORD, Plan, _materialise, load_record, sha256

TOML_BEGIN = "# skill-kit {kit} {cid} begin"
TOML_END = "# skill-kit {kit} {cid} end"


class InstallRefused(ValueError):
    pass


@dataclass
class Report:
    kit: str
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # edited since install: left in place
    missing: list[str] = field(default_factory=list)
    drifted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.missing and not self.drifted


# ── writing ─────────────────────────────────────────────────────────────────


def _write_atomic(path: Path, data: bytes, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".skill-kit-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, 0o755 if executable else 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise InstallRefused(f"{path} is not a JSON object; leaving it unchanged")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _write_atomic(path, (json.dumps(data, indent=2) + "\n").encode())


def apply(plan: Plan, *, replace: bool = False) -> Report:
    """Install. Refuses on validation errors or unresolved conflicts unless
    `replace` resolves every file conflict as "replace" (name clashes and
    conflicts_with still refuse)."""
    if plan.errors:
        raise InstallRefused("validation errors:\n" + "\n".join(str(f) for f in plan.errors))
    hard = [c for c in plan.conflicts if c.kind in ("conflicts-with", "name-clash", "other-kit")]
    if hard:
        raise InstallRefused(
            "conflicts:\n" + "\n".join(f"{c.path or c.component}: {c.detail}" for c in hard)
        )
    soft = [c for c in plan.conflicts if c.kind == "file-exists"]
    if soft and not replace:
        raise InstallRefused(
            "these files exist and are not from this kit (pass --replace to overwrite):\n"
            + "\n".join(f"{c.path}: {c.detail}" for c in soft)
        )
    kit, project = plan.kit, plan.project
    report = Report(kit.name)
    record = load_record(project)
    previous = record["kits"].get(kit.name)
    if previous:
        # Idempotent: an installed kit is verified and repaired, never duplicated.
        _remove_entries(project, previous, Report(kit.name), force=True)
    entries: list[dict[str, Any]] = []
    for p in plan.placements:
        if p.op in ("write", "copy-tree"):
            for rel, data in _materialise(p):
                _write_atomic(project / rel, data, executable=p.executable or _is_script(rel))
                entries.append(
                    {"path": rel, "how": "file", "sha256": sha256(data), "component": p.component}
                )
                report.written.append(rel)
        elif p.op == "json-merge":
            entries.append(_json_merge(project, p))
            report.written.append(p.path)
        elif p.op == "toml-block":
            entries.append(_toml_block(project, kit, p))
            report.written.append(p.path)
        elif p.op == "agents-block":
            entries.append(_agents_block(project, kit, p))
            report.written.append(p.path)
        elif p.op == "claude-import":
            entry = _claude_import(project)
            if entry:
                entries.append(entry)
                report.written.append("CLAUDE.md")
        elif p.op == "check" and p.status == adapters.MISSING:
            report.notes.append(f"{p.path}: not installed on this machine. {p.note}")
    record["kits"][kit.name] = {
        "version": kit.version,
        "source": str(kit.root),
        "harnesses": plan.harnesses,
        "options": plan.options,
        "installed_at": int(time.time()),
        "components": sorted({kit.component_id(c) for c in kit.components}),
        "entries": entries,
    }
    _write_json(project / RECORD, record)
    report.written.append(RECORD)
    return report


def _is_script(rel: str) -> bool:
    return rel.endswith((".sh", ".py")) and "/hooks/" in rel


def _json_merge(project: Path, p: adapters.Placement) -> dict[str, Any]:
    """Add exactly the items in `p.data`; record what was added (and any
    value replaced) so uninstall can take out exactly that."""
    path = project / p.path
    config = _read_json(path)
    added: dict[str, Any] = {"lists": [], "keys": [], "replaced": []}
    _merge_into(config, p.data or {}, [], added)
    _write_json(path, config)
    return {"path": p.path, "how": "json", "component": p.component, **added}


def _merge_into(
    target: dict[str, Any], additions: dict[str, Any], trail: list[str], added: dict[str, Any]
) -> None:
    for key, value in additions.items():
        here = trail + [key]
        if isinstance(value, list):
            current = target.setdefault(key, [])
            if not isinstance(current, list):
                raise InstallRefused(f"{'.'.join(here)} is not a list; leaving the file unchanged")
            for item in value:
                if item not in current:
                    current.append(copy.deepcopy(item))
                    added["lists"].append({"at": here, "item": item})
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_into(target[key], value, here, added)
        elif isinstance(value, dict) and key not in target:
            target[key] = copy.deepcopy(value)
            added["keys"].append({"at": here})
        else:
            if key in target and target[key] != value:
                added["replaced"].append({"at": here, "was": target[key]})
            elif key not in target:
                added["keys"].append({"at": here})
            target[key] = copy.deepcopy(value)


def _toml_block(project: Path, kit: m.Kit, p: adapters.Placement) -> dict[str, Any]:
    path = project / p.path
    text = path.read_text() if path.is_file() else ""
    begin = TOML_BEGIN.format(kit=kit.name, cid=p.component)
    end = TOML_END.format(kit=kit.name, cid=p.component)
    text = _strip_block(text, begin, end)
    block = f"{begin}\n{(p.data or {}).get('text', '').rstrip()}\n{end}\n"
    text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    _write_atomic(path, text.encode())
    return {
        "path": p.path,
        "how": "toml-block",
        "component": p.component,
        "begin": begin,
        "end": end,
    }


def _strip_block(text: str, begin: str, end: str) -> str:
    pattern = re.compile(re.escape(begin) + r"\n.*?" + re.escape(end) + r"\n?", re.S)
    return pattern.sub("", text)


def _agents_block(project: Path, kit: m.Kit, p: adapters.Placement) -> dict[str, Any]:
    """Knowledge into AGENTS.md. A folder that uses DuckTerm's typed rules
    (.duckterm-rules.json present) gets a rule block through the one writer;
    any other AGENTS.md gets an attributed block and keeps every other byte."""
    name = (p.data or {})["name"]
    text = (p.content or b"").decode().strip()
    rule_id = p.component
    if (project / agents_rules.RULES_FILENAME).is_file():
        rules = [r for r in agents_rules.load_rules(project) if r.id != rule_id]
        rules.append(
            agents_rules.RuleBlock(
                id=rule_id,
                text=text,
                status="active",
                source="kit",
                heading=f"Skill kit {kit.name}",
                added=time.strftime("%Y-%m-%d"),
                kit=kit.name,
                kit_version=kit.version,
            )
        )
        agents_rules.save_rules(project, rules)
        return {
            "path": agents_rules.RULES_FILENAME,
            "how": "rule",
            "component": p.component,
            "rule_id": rule_id,
        }
    path = project / "AGENTS.md"
    current = path.read_text() if path.is_file() else ""
    open_tag = adapters.ATTR_OPEN.format(kit=kit.name, name=name, version=kit.version)
    close_tag = adapters.ATTR_CLOSE.format(kit=kit.name, name=name)
    current = _strip_attributed(current, kit.name, name)
    block = f"{open_tag}\n{text}\n{close_tag}\n"
    created = not path.is_file()
    new = (current.rstrip("\n") + "\n\n" if current.strip() else "") + block
    _write_atomic(path, new.encode())
    return {
        "path": "AGENTS.md",
        "how": "block",
        "component": p.component,
        "kit": kit.name,
        "name": name,
        "created": created,
    }


def _strip_attributed(text: str, kit: str, name: str) -> str:
    pattern = re.compile(
        re.escape(f"<!-- skill-kit {kit}:{name} ")
        + r"v[^>]*-->\n.*?"
        + re.escape(f"<!-- /skill-kit {kit}:{name} -->")
        + r"\n?",
        re.S,
    )
    return pattern.sub("", text)


def _claude_import(project: Path) -> dict[str, Any] | None:
    path = project / "CLAUDE.md"
    current = path.read_text() if path.is_file() else ""
    if "@AGENTS.md" in current:
        return {"path": "CLAUDE.md", "how": "import-adopted", "component": "import"}
    created = not path.is_file()
    block = f"{adapters.IMPORT_OPEN}\n@AGENTS.md\n{adapters.IMPORT_CLOSE}\n"
    new = (current.rstrip("\n") + "\n\n" if current.strip() else "") + block
    _write_atomic(path, new.encode())
    return {"path": "CLAUDE.md", "how": "import", "component": "import", "created": created}


# ── uninstall / verify ──────────────────────────────────────────────────────


def uninstall(project: Path, kit_name: str) -> Report:
    project = Path(project)
    record = load_record(project)
    entry = record["kits"].get(kit_name)
    report = Report(kit_name)
    if entry is None:
        report.notes.append(f"{kit_name} is not installed here")
        return report
    _remove_entries(project, entry, report, force=False)
    del record["kits"][kit_name]
    if record["kits"]:
        _write_json(project / RECORD, record)
    else:
        (project / RECORD).unlink(missing_ok=True)
        _prune(project / ".duckterm")
    report.notes.append(
        "Keychain credentials, provider consent and CLI tools stay: a kit is not access"
    )
    return report


def _remove_entries(project: Path, entry: dict[str, Any], report: Report, *, force: bool) -> None:
    for e in reversed(entry.get("entries", [])):
        path = project / e["path"]
        how = e["how"]
        if how == "file":
            if not path.is_file():
                continue
            if not force and sha256(path.read_bytes()) != e["sha256"]:
                report.kept.append(e["path"])
                continue
            path.unlink()
            report.removed.append(e["path"])
            _prune(path.parent, stop=project)
        elif how == "json":
            if not path.is_file():
                continue
            config = _read_json(path)
            for item in e.get("lists", []):
                node = _descend(config, item["at"][:-1])
                if isinstance(node, dict) and isinstance(node.get(item["at"][-1]), list):
                    node[item["at"][-1]] = [x for x in node[item["at"][-1]] if x != item["item"]]
                    if not node[item["at"][-1]]:
                        del node[item["at"][-1]]
            for item in e.get("keys", []):
                node = _descend(config, item["at"][:-1])
                if isinstance(node, dict):
                    node.pop(item["at"][-1], None)
            for item in e.get("replaced", []):
                node = _descend(config, item["at"][:-1])
                if isinstance(node, dict):
                    node[item["at"][-1]] = item["was"]
            _drop_empty(config)
            if config:
                _write_json(path, config)
            else:
                path.unlink()
            report.removed.append(e["path"])
        elif how == "toml-block":
            if path.is_file():
                text = _tidy(_strip_block(path.read_text(), e["begin"], e["end"]))
                if text:
                    _write_atomic(path, text.encode())
                else:
                    path.unlink()
                report.removed.append(e["path"])
        elif how == "rule":
            rules = [r for r in agents_rules.load_rules(project) if r.id != e["rule_id"]]
            agents_rules.save_rules(project, rules)
            report.removed.append(f"rule {e['rule_id']}")
        elif how == "block":
            if path.is_file():
                text = _tidy(_strip_attributed(path.read_text(), e["kit"], e["name"]))
                if text.strip() or not e.get("created"):
                    _write_atomic(path, text.encode())
                else:
                    path.unlink()
                report.removed.append(f"AGENTS.md block {e['kit']}:{e['name']}")
        elif how == "import":
            if path.is_file():
                text = _tidy(
                    _strip_block(path.read_text(), adapters.IMPORT_OPEN, adapters.IMPORT_CLOSE)
                )
                if text.strip() or not e.get("created"):
                    _write_atomic(path, text.encode())
                else:
                    path.unlink()
                report.removed.append("CLAUDE.md import block")


def _tidy(text: str) -> str:
    """Removing a block must not leave its blank line behind: the owner's file
    ends exactly as it did before the block was appended."""
    return text.rstrip("\n") + "\n" if text.strip() else ""


def _descend(node: Any, trail: list[str]) -> Any:
    for key in trail:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _drop_empty(node: dict[str, Any]) -> None:
    for key in list(node):
        value = node[key]
        if isinstance(value, dict):
            _drop_empty(value)
            if not value:
                del node[key]
    if set(node) == {"version"}:  # a Copilot hooks file with nothing left in it
        node.clear()


def _prune(directory: Path, stop: Path | None = None) -> None:
    while directory.is_dir() and directory != stop and not any(directory.iterdir()):
        directory.rmdir()
        directory = directory.parent


def verify(project: Path) -> list[Report]:
    project = Path(project)
    out = []
    for name, entry in load_record(project)["kits"].items():
        report = Report(name)
        for e in entry.get("entries", []):
            if e["how"] != "file":
                continue
            path = project / e["path"]
            if not path.is_file():
                report.missing.append(e["path"])
            elif sha256(path.read_bytes()) != e["sha256"]:
                report.drifted.append(e["path"])
        out.append(report)
    return out
