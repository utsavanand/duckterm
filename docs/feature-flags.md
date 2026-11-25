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
and the gated routes answer. Without the flag those routes return 403;
dashboard routes also carry `code: feature_unavailable`. A name that is no longer under development is
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
| `skill_kits` | Settings → Skill kits (add, review, install, verify, Create team, uninstall), a session's Flows tab, the `/kits/*`, `/api/v1/session/kits/*` and legacy `/harnesses` routes, and `duckterm kit` validate/plan/install/verify/uninstall/run/runs. |
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

The inventory includes both release and development switches, once each.
`home` and `background_rule_suggestions` still use `flags.json`; creating a
release configuration does not disable or enable those separate switches.
Skill kits remain experimental, but their row is listed only above.

## In development

| Flag | What it gates | Since | Ships when |
|------|---------------|-------|------------|
| `home` | The Home page: the first page the dashboard opens on, a 12-column grid of movable widgets (Ask Oracle chat, Teams, Autopilot, pull requests, usage, Needs you), and `GET`/`PUT /layouts/home`. Layout is saved in `home-layout.json`. The Control Tower stays until Home replaces it. | 2026-11-19 | Owner has used it on real data, Autopilot has a backend to open, and the Control Tower is folded in. Owner's call. |
| `background_rule_suggestions` | Reconcile original observations after checkpoints in Code/Full. Off by default; pending proposals and capture fingerprints stay in DuckTerm home under `rule-reviews/`, keyed by canonical directory path. The manual Suggest button is independent. | 2026-11-21 | Security and QA approve background capture. Owner’s call. |
| `autopilot` | The Autopilot sidebar row and page, putting a flow on autopilot, and the `/api/autopilot` routes. Runs flows in Docker sandboxes on this Mac with the owner's Claude subscription; the GitHub issues trigger is polled every 2 minutes. | 2026-11-22 | Runs open pull requests and merge behind the approval gates, and the owner has used it on a real repository. Owner's call. |

## Graduated

None yet. When a flag ships, move its row here with the release that removed it.

## Adding a flag

1. Add the name to `DEVELOPMENT` in `src/duckterm/editions.py` and route its
   paths to it in `_path_feature`.
2. Gate the dashboard surface with `useFeature("<flag>")`; add the name to the
   `Feature` type in `web/src/edition.tsx` and to `DEVELOPMENT` in
   `web/src/test/editionFeatures.ts`.
3. Add a row above. `tests/unit/test_feature_flags.py` fails until it exists.
