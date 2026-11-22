# Feature flags

Work that is still being built ships inside the app but stays off for
everyone. An instance turns a flag on by listing it in `flags.json` in its
DuckTerm home (`~/.duckterm/flags.json` unless `DUCKTERM_HOME` says otherwise)
and restarting:

```json
{"enabled": ["skill_kits"]}
```

The server adds enabled flags to the edition's feature list, so `GET /edition`
and the inbox's `features` show them, the dashboard renders the gated surfaces,
and the gated routes answer. Without the flag those routes return 403 with
`code: feature_unavailable`. A name that is no longer under development is
ignored, so a left-over file never blocks startup once a feature ships; a
malformed file stops startup, like a bad `edition.json`.

A flag is a local opt-in, not a licence boundary and not a security boundary.
Any process that can write the DuckTerm home, or the target of a symlink there,
can turn a flag on. Flags keep unfinished work out of other people's way; they
do not keep anyone out.

## Release switches

All six switches default off in distributed Lite, Code and Full builds. Local
Claude/Codex sessions and their existing sign-in continue to work.

| Flag | Controls hidden and operations blocked while off |
|------|--------------------------------------------------|
| `cross_computer_collaboration` | Cross-computer collaboration setup and exchange. Local inbox remains available. |
| `skill_kits` | Skill-kit management, installation and removal. |
| `harness_management` | Harness installation and setup. Existing local harnesses remain usable. |
| `byok` | API-key configuration and use, including deleting a saved key. Existing keys are retained; enable the flag to remove one through DuckTerm. |
| `byoc` | Cloud/remote workspace management, including native Mac Settings controls. Available only in Full even when opted in. |
| `backup` | Backup controls and operations. Existing backups are retained. Cloud destinations also require BYOC. |

These switches are configured in `feature-flags.json` in the instance home:

```json
{"flags": {"skill_kits": true, "backup": true}}
```

Restart DuckTerm after changes. Settings → Feature flags shows effective
availability. Missing or invalid release configuration disables these switches;
when the release file is missing, the legacy skill-kit exception below applies.
Configuration belongs to the installation and must not be bundled in releases.

`feature-flags.json`, when present, is authoritative for those switches, including
`skill_kits`. A missing release-switch file preserves the older `flags.json`
skill-kit opt-in; it does not enable any other release switch. An explicit false
or an omitted switch in `feature-flags.json` disables it. No configuration is
rewritten during this compatibility read.

## In development

| Flag | What it gates | Since | Ships when |
|------|---------------|-------|------------|
| `skill_kits` | Settings → Skill kits (add a kit by folder, view its contents, remove it), the `/harnesses` routes, and the session panel's "Skill kit" row. Being built against the Skill Kit Specification (draft 8). | 2026-11-16 | The specification is approved and the install wizard is built. Owner's call. |

## Graduated

None yet. When a flag ships, move its row here with the release that removed it.

## Adding a flag

1. Add the name to `DEVELOPMENT` in `src/duckterm/editions.py` and route its
   paths to it in `_path_feature`.
2. Gate the dashboard surface with `useFeature("<flag>")`; add the name to the
   `Feature` type in `web/src/edition.tsx` and to `DEVELOPMENT` in
   `web/src/test/editionFeatures.ts`.
3. Add a row above. `tests/unit/test_feature_flags.py` fails until it exists.
