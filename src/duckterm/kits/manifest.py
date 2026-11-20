"""`skill-kit.toml` → Kit. Parsing only; see validate.py for the authoring rules."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST = "skill-kit.toml"
LEGACY_MANIFEST = "duckterm-harness.json"
KINDS = ("knowledge", "tool", "skill", "agent", "hook", "permission", "flow", "settings")
HARNESSES = ("claude-code", "codex", "copilot", "opencode")
NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


class ManifestError(ValueError):
    pass


@dataclass
class Component:
    kind: str
    name: str
    fields: dict[str, Any]
    index: int

    @property
    def path(self) -> str | None:
        value = self.fields.get("path")
        return str(value) if value else None

    @property
    def only_harness(self) -> list[str] | None:
        only = self.fields.get("only")
        if isinstance(only, dict) and isinstance(only.get("harness"), list):
            return [str(h) for h in only["harness"]]
        return None

    @property
    def when(self) -> dict[str, str]:
        when = self.fields.get("when")
        return {str(k): str(v) for k, v in when.items()} if isinstance(when, dict) else {}

    def applies(self, harness: str, options: dict[str, str]) -> bool:
        only = self.only_harness
        if only is not None and harness not in only:
            return False
        return all(options.get(k) == v for k, v in self.when.items())


@dataclass
class Kit:
    name: str
    description: str
    version: str
    root: Path
    components: list[Component]
    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    conflicts_with: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    legacy: bool = False

    def component_id(self, component: Component) -> str:
        return f"{self.name}/{component.kind}/{component.name}"

    def default_options(self) -> dict[str, str]:
        return {
            name: str(spec["default"])
            for name, spec in self.options.items()
            if isinstance(spec, dict) and "default" in spec
        }

    def by_kind(self, kind: str) -> list[Component]:
        return [c for c in self.components if c.kind == kind]


def component_name(kind: str, fields: dict[str, Any]) -> str:
    """The name every component is addressed by: `<kit>/<kind>/<name>`.
    Path-backed kinds take the file or folder name; inline kinds derive one."""
    if fields.get("name"):
        return str(fields["name"])
    path = fields.get("path")
    if path:
        p = Path(str(path))
        return p.stem if p.suffix else p.name
    if kind == "tool":
        for form in ("mcp", "cli"):
            spec = fields.get(form)
            if isinstance(spec, dict):
                return str(spec.get("name") or spec.get("binary") or form)
    if kind == "hook":
        command = Path(str(fields.get("command", "hook")))
        return f"{fields.get('event', 'event')}-{command.stem}"
    if kind == "permission":
        return "rules"
    return kind


def load(root: Path) -> Kit:
    """Read a kit from its directory. A legacy `duckterm-harness.json` is
    accepted with `legacy=True` so callers can warn; it carries no components."""
    root = Path(root)
    manifest = root / MANIFEST
    if manifest.is_file():
        try:
            raw = tomllib.loads(manifest.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise ManifestError(f"{manifest}: {exc}") from exc
        return _from_raw(raw, root)
    legacy = root / LEGACY_MANIFEST
    if legacy.is_file():
        try:
            data = json.loads(legacy.read_text())
        except json.JSONDecodeError as exc:
            raise ManifestError(f"{legacy}: {exc}") from exc
        if not isinstance(data, dict):
            raise ManifestError(f"{legacy}: expected an object")
        return Kit(
            name=str(data.get("name") or root.name),
            description=str(data.get("description") or ""),
            version=str(data.get("version") or "0.0.0"),
            root=root,
            components=[],
            raw=data,
            legacy=True,
        )
    raise ManifestError(f"{root} has no {MANIFEST}")


def _from_raw(raw: dict[str, Any], root: Path) -> Kit:
    missing = [k for k in ("manifest_version", "name", "description", "version") if k not in raw]
    if missing:
        raise ManifestError(f"{MANIFEST}: missing {', '.join(missing)}")
    if raw["manifest_version"] != 1:
        raise ManifestError(f"{MANIFEST}: manifest_version {raw['manifest_version']!r} unknown")
    entries = raw.get("components")
    if not isinstance(entries, list):
        raise ManifestError(f"{MANIFEST}: `components` must be an array of tables")
    components: list[Component] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or "kind" not in entry:
            raise ManifestError(f"{MANIFEST}: components[{index}] needs a `kind`")
        kind = str(entry["kind"])
        components.append(Component(kind, component_name(kind, entry), dict(entry), index))
    options = raw.get("options") or {}
    if not isinstance(options, dict):
        raise ManifestError(f"{MANIFEST}: `options` must be a table")
    teams = raw.get("teams") or []
    return Kit(
        name=str(raw["name"]),
        description=str(raw["description"]),
        version=str(raw["version"]),
        root=root,
        components=components,
        options={str(k): dict(v) for k, v in options.items() if isinstance(v, dict)},
        conflicts_with=[str(c) for c in raw.get("conflicts_with") or []],
        teams=[str(t) for t in teams] if isinstance(teams, list) else [],
        raw=raw,
    )


def frontmatter(text: str) -> dict[str, str]:
    """The flat `key: value` pairs of a Markdown frontmatter block. Folded
    (`>`) and literal (`|`) scalars are joined; lists and nesting are returned
    as their raw text, which is enough for name/description checks."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    key: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line[:1] in (" ", "\t") and key:
            out[key] = (out[key] + " " + line.strip()).strip()
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")
        out[key] = "" if value in (">", "|", ">-", "|-") else value
    return out
