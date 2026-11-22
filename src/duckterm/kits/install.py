"""Apply a plan, write the record, uninstall from the record, verify (§5, §9,
§III.4, §III.12). Every write is temp-file + rename.

Two records. `.duckterm/installed.json` travels with the repository and says
what a kit wrote; a copy of each kit's entry lives under the DuckTerm home,
keyed by project path. The repository copy is advisory: a clone controls it.
Only when it matches the home copy does uninstall restore the bytes it saved
("before", "was"); otherwise it strips what a kit of that name would have
written (attributed blocks, kit-owned rules, copied files that still hash)
and leaves every other byte, including the owner's config JSON, alone."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from duckterm.core import agents_rules
from duckterm.helpers import instance
from duckterm.kits import adapters
from duckterm.kits import manifest as m
from duckterm.kits.plan import RECORD, PathEscape, Plan, _materialise, inside, load_record, sha256

TOML_BEGIN = "# skill-kit {kit} {cid} begin"
TOML_END = "# skill-kit {kit} {cid} end"


class InstallRefused(ValueError):
    pass


HARD_CONFLICTS = ("conflicts-with", "name-clash", "other-kit", "escapes")


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


def _write_atomic(
    path: Path, data: bytes, *, executable: bool = False, created: set[Path] | None = None
) -> None:
    """`created` collects the directories this write had to make, so uninstall
    prunes those and never a folder the owner had already."""
    folder = path.parent
    while created is not None and not folder.exists():
        created.add(folder)
        folder = folder.parent
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


EDITED = ("json", "toml-block", "block", "import")


def _snapshot(path: Path) -> str | None:
    """The file as it was before this edit, or None when it did not exist.
    surrogateescape keeps odd bytes round-trippable through the JSON record."""
    return path.read_bytes().decode("utf-8", "surrogateescape") if path.is_file() else None


def _edited(path: Path, before: str | None) -> dict[str, Any]:
    return {"before": before, "after_sha256": sha256(path.read_bytes())}


def _target(project: Path, rel: str) -> Path:
    try:
        return inside(project, rel)
    except PathEscape as exc:
        raise InstallRefused(str(exc)) from exc


KNOWN_HOW = ("file", "json", "toml-block", "block", "import", "import-adopted", "rule")


def _entry_path(project: Path, e: Any) -> Path:
    """The record lives in the repository, so a clone controls it: every
    entry is checked before it names a file to delete, rewrite or restore."""
    if (
        not isinstance(e, dict)
        or e.get("how") not in KNOWN_HOW
        or not isinstance(e.get("path"), str)
    ):
        raise InstallRefused(f"{RECORD}: malformed entry {e!r}")
    return _target(project, e["path"])


def _home_record(project: Path) -> Path:
    key = hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:24]
    return instance.home() / "kits" / "records" / f"{key}.json"


def _load_home(project: Path) -> dict[str, Any]:
    path = _home_record(project)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    kits = data.get("kits") if isinstance(data, dict) else None
    return kits if isinstance(kits, dict) else {}


def _save_home(project: Path, kits: dict[str, Any]) -> None:
    path = _home_record(project)
    if kits:
        _write_json(path, {"project": str(project.resolve()), "kits": kits})
    else:
        path.unlink(missing_ok=True)


def _trusted(project: Path, kit_name: str, entry: dict[str, Any]) -> bool:
    """The repository's entry is only believed when this DuckTerm wrote it:
    its saved bytes, replaced values and list items are otherwise an
    attacker's text with a path to write it to."""
    return _load_home(project).get(kit_name) == entry


def _restore(path: Path, before: str | None) -> None:
    if before is None:
        path.unlink(missing_ok=True)
    else:
        _write_atomic(path, before.encode("utf-8", "surrogateescape"))


def apply(plan: Plan, *, replace: bool = False) -> Report:
    """Install. Refuses on validation errors or unresolved conflicts unless
    `replace` resolves every file conflict as "replace" (name clashes and
    conflicts_with still refuse)."""
    if plan.errors:
        raise InstallRefused("validation errors:\n" + "\n".join(str(f) for f in plan.errors))
    hard = [c for c in plan.conflicts if c.kind in HARD_CONFLICTS]
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
        # Idempotent: an installed kit is taken out by its record and put back,
        # never duplicated. A kit file edited since install is kept here and
        # was already a file-exists conflict in the plan, so it is only
        # overwritten under --replace.
        _remove_entries(project, kit.name, previous, report, _trusted(project, kit.name, previous))
        _prune_dirs(project, previous)
    entries: list[dict[str, Any]] = []
    # Folders the earlier install made and that still stand (.duckterm holds
    # the record) stay this kit's to prune.
    created: set[Path] = {
        project / str(d) for d in (previous or {}).get("dirs", []) if (project / str(d)).is_dir()
    }
    for p in plan.placements:
        if p.op in ("write", "copy-tree"):
            for rel, data in _materialise(p):
                target = _target(project, rel)
                _write_atomic(
                    target, data, executable=p.executable or _is_script(rel), created=created
                )
                entries.append(
                    {"path": rel, "how": "file", "sha256": sha256(data), "component": p.component}
                )
                report.written.append(rel)
        elif p.op == "json-merge":
            entries.append(_json_merge(project, p, created))
            report.written.append(p.path)
        elif p.op == "toml-block":
            entries.append(_toml_block(project, kit, p, created))
            report.written.append(p.path)
        elif p.op == "agents-block":
            entries.append(_agents_block(project, kit, p, created))
            report.written.append(p.path)
        elif p.op == "claude-import":
            entry = _claude_import(project, created)
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
        "procedures": _procedures(kit),
        "details": _details(kit),
        "entries": entries,
    }
    record_path = _target(project, RECORD)
    if not record_path.parent.exists():
        created.add(record_path.parent)
    record["kits"][kit.name]["dirs"] = sorted(d.relative_to(project).as_posix() for d in created)
    _write_json(record_path, record)
    home = _load_home(project)
    home[kit.name] = record["kits"][kit.name]
    _save_home(project, home)
    report.written.append(RECORD)
    return report


def _procedures(kit: m.Kit) -> list[dict[str, Any]]:
    """The team templates a kit ships, summarised into the record so the
    Skill kits page can list them without the kit folder."""
    from duckterm.kits import api

    return api.procedures(kit)


def _details(kit: m.Kit) -> dict[str, Any]:
    from duckterm.kits import api

    return api.details(kit)


def _is_script(rel: str) -> bool:
    return rel.endswith((".sh", ".py")) and "/hooks/" in rel


def _json_merge(project: Path, p: adapters.Placement, created: set[Path]) -> dict[str, Any]:
    """Add exactly the items in `p.data`; record what was added (and any
    value replaced) so uninstall can take out exactly that."""
    path = _target(project, p.path)
    before = _snapshot(path)
    config = _read_json(path)
    added: dict[str, Any] = {"lists": [], "keys": [], "replaced": []}
    _merge_into(config, p.data or {}, [], added)
    _write_atomic(path, (json.dumps(config, indent=2) + "\n").encode(), created=created)
    entry = {"path": p.path, "how": "json", "component": p.component}
    return {**entry, **added, **_edited(path, before)}


def _merge_into(
    target: dict[str, Any], additions: dict[str, Any], trail: list[str], added: dict[str, Any]
) -> None:
    for key, value in additions.items():
        here = trail + [key]
        if isinstance(value, list):
            created = key not in target
            current = target.setdefault(key, [])
            if not isinstance(current, list):
                raise InstallRefused(f"{'.'.join(here)} is not a list; leaving the file unchanged")
            for item in value:
                if item not in current:
                    current.append(copy.deepcopy(item))
                    added["lists"].append({"at": here, "item": item, "created": created})
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_into(target[key], value, here, added)
        elif isinstance(value, dict) and key not in target:
            target[key] = copy.deepcopy(value)
            added["keys"].append({"at": here, "value": value})
        else:
            if key in target and target[key] != value:
                added["replaced"].append({"at": here, "was": target[key], "value": value})
            elif key not in target:
                added["keys"].append({"at": here, "value": value})
            target[key] = copy.deepcopy(value)


def _toml_block(
    project: Path, kit: m.Kit, p: adapters.Placement, created: set[Path]
) -> dict[str, Any]:
    path = _target(project, p.path)
    before = _snapshot(path)
    text = before or ""
    begin = TOML_BEGIN.format(kit=kit.name, cid=p.component)
    end = TOML_END.format(kit=kit.name, cid=p.component)
    text = _strip_block(text, begin, end)
    block = f"{begin}\n{(p.data or {}).get('text', '').rstrip()}\n{end}\n"
    text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    _write_atomic(path, text.encode(), created=created)
    return {
        "path": p.path,
        "how": "toml-block",
        "component": p.component,
        "begin": begin,
        "end": end,
        "block": block,
        **_edited(path, before),
    }


def _strip_block(text: str, begin: str, end: str) -> str:
    pattern = re.compile(re.escape(begin) + r"\n.*?" + re.escape(end) + r"\n?", re.S)
    return pattern.sub("", text)


def _agents_block(
    project: Path, kit: m.Kit, p: adapters.Placement, created: set[Path]
) -> dict[str, Any]:
    """Knowledge into AGENTS.md. A folder that uses DuckTerm's typed rules
    (.duckterm-rules.json present) gets a rule block through the one writer;
    any other AGENTS.md gets an attributed block and keeps every other byte."""
    name = (p.data or {})["name"]
    text = (p.content or b"").decode().strip()
    rule_id = p.component
    path = _target(project, "AGENTS.md")
    if _target(project, agents_rules.RULES_FILENAME).is_file():
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
            "rule": {"text": text, "status": "active", "scope": "all", "kit_version": kit.version},
        }
    before = _snapshot(path)
    open_tag = adapters.ATTR_OPEN.format(kit=kit.name, name=name, version=kit.version)
    close_tag = adapters.ATTR_CLOSE.format(kit=kit.name, name=name)
    current = _strip_attributed(before or "", kit.name, name)
    block = f"{open_tag}\n{text}\n{close_tag}\n"
    new = (current.rstrip("\n") + "\n\n" if current.strip() else "") + block
    _write_atomic(path, new.encode(), created=created)
    return {
        "path": "AGENTS.md",
        "how": "block",
        "component": p.component,
        "kit": kit.name,
        "name": name,
        "block": block,
        **_edited(path, before),
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


def _claude_import(project: Path, created: set[Path]) -> dict[str, Any] | None:
    path = _target(project, "CLAUDE.md")
    before = _snapshot(path)
    current = before or ""
    if "@AGENTS.md" in current:
        return {"path": "CLAUDE.md", "how": "import-adopted", "component": "import"}
    block = f"{adapters.IMPORT_OPEN}\n@AGENTS.md\n{adapters.IMPORT_CLOSE}\n"
    new = (current.rstrip("\n") + "\n\n" if current.strip() else "") + block
    _write_atomic(path, new.encode(), created=created)
    return {"path": "CLAUDE.md", "how": "import", "component": "import", **_edited(path, before)}


# ── uninstall / verify ──────────────────────────────────────────────────────


def uninstall(project: Path, kit_name: str) -> Report:
    project = Path(project)
    record = load_record(project)
    entry = record["kits"].get(kit_name)
    report = Report(kit_name)
    if entry is None:
        report.notes.append(f"{kit_name} is not installed here")
        return report
    if not m.NAME_RE.match(kit_name):
        raise InstallRefused(f"{kit_name!r} is not a kit name")
    trusted = _trusted(project, kit_name, entry)
    if not trusted:
        report.notes.append(
            f"{RECORD} was not written by this DuckTerm: kit text was stripped, saved "
            "bytes were not restored, and config JSON was left as it is"
        )
    _remove_entries(project, kit_name, entry, report, trusted)
    del record["kits"][kit_name]
    record_path = _target(project, RECORD)
    if record["kits"]:
        _write_json(record_path, record)
    else:
        record_path.unlink(missing_ok=True)
    _prune_dirs(project, entry)
    home = _load_home(project)
    home.pop(kit_name, None)
    _save_home(project, home)
    report.notes.append(
        "Keychain credentials, provider consent and CLI tools stay: a kit is not access"
    )
    return report


def _remove_entries(
    project: Path, kit_name: str, entry: dict[str, Any], report: Report, trusted: bool
) -> None:
    for e in reversed(entry.get("entries", [])):
        path = _entry_path(project, e)
        how = e["how"]
        if how == "file":
            if not path.is_file():
                continue
            if sha256(path.read_bytes()) != e["sha256"]:
                report.kept.append(e["path"])
                continue
            path.unlink()
            report.removed.append(e["path"])
        elif how == "rule":
            rule_id = e["rule_id"]
            _target(project, "AGENTS.md")  # save_rules renders it beside the rules file
            rules = agents_rules.load_rules(project)
            keep = [r for r in rules if not (r.id == rule_id and r.kit == kit_name)]
            if len(keep) != len(rules):
                agents_rules.save_rules(project, keep)
                report.removed.append(f"rule {rule_id}")
        elif how in EDITED:
            if not path.is_file():
                continue
            if trusted and sha256(path.read_bytes()) == e["after_sha256"]:
                _restore(path, e["before"])
            elif trusted:
                _remove_additions(path, e)
            elif how == "json":
                report.kept.append(e["path"])
                continue
            else:
                _strip_kit_text(path, kit_name, e)
            report.removed.append(_label(e))


def _component_name(kit_name: str, e: dict[str, Any]) -> str:
    """The component's own name, taken from an id of the form
    <kit>/<kind>/<name>; anything else in that field is refused."""
    parts = str(e.get("component", "")).split("/")
    if len(parts) != 3 or parts[0] != kit_name or not m.COMPONENT_RE.match(parts[2]):
        raise InstallRefused(f"{RECORD}: entry names a component not of kit {kit_name}")
    return parts[2]


def _strip_kit_text(path: Path, kit_name: str, e: dict[str, Any]) -> None:
    """Untrusted record: remove only text between markers this kit would have
    written, derived from the kit and component names rather than read from
    the record, and never write anything the record supplied."""
    text = path.read_text()
    if e["how"] == "toml-block":
        name = _component_name(kit_name, e)
        cid = f"{kit_name}/{str(e['component']).split('/')[1]}/{name}"
        begin = TOML_BEGIN.format(kit=kit_name, cid=cid)
        text = _strip_block(text, begin, TOML_END.format(kit=kit_name, cid=cid))
    elif e["how"] == "block":
        text = _strip_attributed(text, kit_name, _component_name(kit_name, e))
    else:
        text = _strip_block(text, adapters.IMPORT_OPEN, adapters.IMPORT_CLOSE)
    text = text.rstrip("\n") + "\n" if text.strip() else ""
    if text:
        _write_atomic(path, text.encode())
    else:
        path.unlink()


def _label(e: dict[str, Any]) -> str:
    if e["how"] == "block":
        return f"AGENTS.md block {e['kit']}:{e['name']}"
    if e["how"] == "import":
        return "CLAUDE.md import block"
    return str(e["path"])


def _remove_additions(path: Path, e: dict[str, Any]) -> None:
    """The owner changed the file after the install: take out only what the
    record says this entry added and leave every other byte alone."""
    how = e["how"]
    if how == "json":
        config = _read_json(path)
        for item in e.get("lists", []):
            node = _descend(config, item["at"][:-1])
            if isinstance(node, dict) and isinstance(node.get(item["at"][-1]), list):
                node[item["at"][-1]] = [x for x in node[item["at"][-1]] if x != item["item"]]
                if not node[item["at"][-1]] and item.get("created"):
                    del node[item["at"][-1]]
        for item in e.get("keys", []):
            node = _descend(config, item["at"][:-1])
            if isinstance(node, dict):
                _unmerge(node, {item["at"][-1]: item["value"]})
        for item in e.get("replaced", []):
            node = _descend(config, item["at"][:-1])
            if isinstance(node, dict) and node.get(item["at"][-1]) == item["value"]:
                node[item["at"][-1]] = item["was"]
        if config or e["before"] is not None:
            _write_json(path, config)
        else:
            path.unlink()
        return
    text = path.read_text()
    if how == "toml-block":
        text = _strip_block(text, e["begin"], e["end"])
    elif how == "block":
        text = _strip_attributed(text, e["kit"], e["name"])
    else:
        text = _strip_block(text, adapters.IMPORT_OPEN, adapters.IMPORT_CLOSE)
    text = text.rstrip("\n") + "\n" if text.strip() else ""
    if text or e["before"] is not None:
        _write_atomic(path, text.encode())
    else:
        path.unlink()


def _contains(actual: Any, expected: Any) -> bool:
    """`expected` is still there, allowing for what other entries or the owner
    added beside it."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            _contains(actual.get(k), v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(item in actual for item in expected)
    return bool(actual == expected)


def _unmerge(target: dict[str, Any], value: dict[str, Any]) -> None:
    """Take `value` back out of `target` item by item, so a key the kit created
    and the owner later added to keeps the owner's part."""
    for key, sub in value.items():
        current = target.get(key)
        if isinstance(sub, dict) and isinstance(current, dict):
            _unmerge(current, sub)
            if not current:
                del target[key]
        elif isinstance(sub, list) and isinstance(current, list):
            target[key] = [x for x in current if x not in sub]
            if not target[key]:
                del target[key]
        elif key in target and current == sub:
            del target[key]


def _descend(node: Any, trail: list[str]) -> Any:
    for key in trail:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _prune_dirs(project: Path, entry: dict[str, Any]) -> None:
    """Only directories the install itself created, deepest first, and only
    while empty: a pre-existing empty folder is the owner's."""
    for rel in sorted(entry.get("dirs", []), key=len, reverse=True):
        folder = _target(project, str(rel))
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def verify(project: Path) -> list[Report]:
    """Every recorded entry, not only copied files: a merged config that was
    deleted is missing, one whose kit values are gone is drifted. The owner's
    own additions to a shared file are neither."""
    project = Path(project)
    out = []
    for name, entry in load_record(project)["kits"].items():
        report = Report(name)
        for e in entry.get("entries", []):
            try:
                path = _entry_path(project, e)
            except InstallRefused as exc:
                report.drifted.append(str(exc))
                continue
            how = e["how"]
            if how == "rule":
                rule = next(
                    (r for r in agents_rules.load_rules(project) if r.id == e["rule_id"]), None
                )
                if rule is None:
                    report.missing.append(f"rule {e['rule_id']}")
                elif not _rule_matches(rule, name, e.get("rule")):
                    report.drifted.append(f"rule {e['rule_id']}")
                continue
            if not path.is_file():
                report.missing.append(e["path"])
                continue
            data = path.read_bytes()
            if how == "file":
                if sha256(data) != e["sha256"]:
                    report.drifted.append(e["path"])
            elif how in EDITED:
                if sha256(data) != e["after_sha256"] and not _additions_present(data, e):
                    report.drifted.append(_label(e))
            elif how == "import-adopted" and "@AGENTS.md" not in data.decode(errors="replace"):
                report.drifted.append("CLAUDE.md import")
        out.append(report)
    return out


def _rule_matches(rule: agents_rules.RuleBlock, kit_name: str, want: Any) -> bool:
    """Installed instructions are only effective while the rule is active, is
    still this kit's and still says what the kit shipped."""
    if not isinstance(want, dict):
        return rule.kit == kit_name and rule.status == "active"
    return (
        rule.kit == kit_name
        and rule.text == want.get("text")
        and rule.status == want.get("status")
        and rule.scope == want.get("scope")
        and rule.kit_version == want.get("kit_version")
    )


def _additions_present(data: bytes, e: dict[str, Any]) -> bool:
    how = e["how"]
    if how == "json":
        try:
            config = json.loads(data)
        except ValueError:
            return False
        if not isinstance(config, dict):
            return False
        for item in e.get("lists", []):
            node = _descend(config, item["at"])
            if not isinstance(node, list) or item["item"] not in node:
                return False
        for item in e.get("keys", []) + e.get("replaced", []):
            if not _contains(_descend(config, item["at"]), item["value"]):
                return False
        return True
    text = data.decode(errors="replace")
    if how == "import":
        return "@AGENTS.md" in text
    return e["block"] in text
