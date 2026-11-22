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

## In development

| Flag | What it gates | Since | Ships when |
|------|---------------|-------|------------|
| `skill_kits` | Settings → Skill kits: a full page listing the kits installed in a workspace's repository and the procedures they ship, the add flow (kit folder → review the plan → install), each kit's page with verify, flows, every component kind with its details, Create team and uninstall; a session's Flows tab with gates answered under their step; the `/kits/*` and `/api/v1/session/kits/*` routes behind them and the legacy `/harnesses` routes; the session panel's "Skill kit" row; the `duckterm kit` CLI (validate, plan, install, verify, uninstall, run, runs). Built against the Skill Kit Specification (draft 11). | 2026-11-16 | The install wizard is built on `plan`/`install` and the owner has run a kit end to end. Owner's call. |
| `background_rule_suggestions` | Reconcile original observations after checkpoints in Code/Full. Off by default; pending proposals and capture fingerprints stay in DuckTerm home under `rule-reviews/`, keyed by canonical directory path. The manual Suggest button is independent. | 2026-11-21 | Security and QA approve background capture. Owner’s call. |

## Graduated

None yet. When a flag ships, move its row here with the release that removed it.

## Adding a flag

1. Add the name to `DEVELOPMENT` in `src/duckterm/editions.py` and route its
   paths to it in `_path_feature`.
2. Gate the dashboard surface with `useFeature("<flag>")`; add the name to the
   `Feature` type in `web/src/edition.tsx` and to `DEVELOPMENT` in
   `web/src/test/editionFeatures.ts`.
3. Add a row above. `tests/unit/test_feature_flags.py` fails until it exists.
