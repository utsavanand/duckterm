import { useEffect, useState } from "react";
import { api, HarnessPreferences, KitEntry, KitProcedure } from "./api";
import { DirBrowser } from "./DirBrowser";
import { teamRequest } from "./teams";
import { Button, Field, inputStyle, Modal, useToast } from "./ui";

type Offer = { kit: KitEntry; procedure: KitProcedure };

// New ▾ › New team, and the MetaHarness card's Create team: the team templates
// every added MetaHarness offers, the folder the team works in, and the
// workspace they become with one session per role. Both doors send the same
// request.
export function NewTeamModal({
  sessionKey,
  existing,
  preselect,
  onClose,
  onOpenKits,
  onCreated,
}: {
  sessionKey?: string;
  existing: string[];
  preselect?: { kit: string; team: string };
  onClose: () => void;
  onOpenKits: () => void;
  onCreated: (folder: string) => void;
}) {
  const toast = useToast();
  const [teams, setTeams] = useState<Offer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [startSessions, setStartSessions] = useState(true);
  const [prefs, setPrefs] = useState<HarnessPreferences | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api.kitsCatalog(sessionKey).then(
      (catalog) => {
        if (!active) return;
        const offered = catalog.kits.flatMap((kit) => (kit.procedures ?? []).map((procedure) => ({ kit, procedure })));
        setTeams(offered);
        const first = offered.length === 1
          ? offered[0]
          : offered.find((o) => preselect && o.kit.name === preselect.kit && o.procedure.name === preselect.team);
        if (first) {
          setChosen(key(first));
          setName(first.procedure.name);
          setPath(first.kit.installed_in[0]?.project ?? "");
        }
      },
      (e: Error) => { if (active) setError(e.message); },
    );
    api.harnessPreferences().then((p) => { if (active) setPrefs(p); }, () => { if (active) setPrefs({ default_harness: null, present: [], effective: null }); });
    return () => { active = false; };
  }, [sessionKey, preselect]);

  const selected = teams?.find((t) => key(t) === chosen) ?? null;
  const roles = selected ? Object.keys(selected.procedure.roles).length : 0;
  const willStart = startSessions && !!prefs?.effective;

  function choose(team: Offer) {
    setChosen(key(team));
    if (!name.trim() || name === selected?.procedure.name) setName(team.procedure.name);
    if (!path.trim() || path === (selected?.kit.installed_in[0]?.project ?? "")) setPath(team.kit.installed_in[0]?.project ?? "");
  }

  async function create() {
    const trimmed = name.trim();
    const folder = path.trim();
    if (!selected || !trimmed || !folder) return;
    if (existing.includes(trimmed)) {
      toast(`Workspace "${trimmed}" already exists`, "err");
      return;
    }
    setBusy(true);
    try {
      const out = await api.kitsCreateTeam(teamRequest(selected.kit, selected.procedure.name, trimmed, folder, willStart), sessionKey);
      const started = out.sessions?.length ?? 0;
      const failed = out.failed ?? [];
      toast(
        `Team ${out.team.team}: workspace "${out.team.folder}" created with ${Object.keys(out.team.roles).length} roles` +
          (started ? `, ${started} session${started === 1 ? "" : "s"} started on ${out.harness}` : "") +
          (failed.length ? `; not started: ${failed.map((f) => `${f.role} (${f.error})`).join(", ")}` : ""),
        failed.length ? "err" : "ok",
      );
      onCreated(out.team.folder);
    } catch (e) {
      toast(`Create team refused: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="New team" onClose={onClose}>
      {error && <p className="rd-modal-hint">Could not load the MetaHarnesses: {error}</p>}
      {!error && teams === null && <p className="rd-modal-hint">Loading…</p>}
      {teams && teams.length === 0 && (
        <>
          <p className="rd-modal-hint">None of the added MetaHarnesses offers a team. A team is a workspace with a procedure, and procedures come from MetaHarnesses.</p>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button onClick={onOpenKits}>Open Settings › MetaHarnesses</Button>
          </div>
        </>
      )}
      {teams && teams.length > 0 && (
        <>
          <fieldset style={{ border: 0, padding: 0, margin: "0 0 12px" }}>
            <legend style={{ fontSize: "0.8125rem", color: "var(--text-soft)", marginBottom: 4 }}>Team</legend>
            {teams.map((team) => {
              const k = key(team);
              return (
                <label key={k} style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 10px", alignItems: "start", padding: "8px 10px", borderRadius: 8, background: chosen === k ? "var(--bg-soft)" : "transparent", cursor: "pointer" }}>
                  <input type="radio" name="team" aria-label={`${team.procedure.name} from ${team.kit.name}`} checked={chosen === k} onChange={() => choose(team)} style={{ marginTop: 3 }} />
                  <span>
                    <strong>{team.procedure.name}</strong>{" "}
                    <span style={{ color: "var(--text-soft)", fontSize: "0.8125rem" }}>from {team.kit.name}</span>
                    {team.procedure.description && <span style={{ display: "block", fontSize: "0.8125rem" }}>{team.procedure.description}</span>}
                    <span style={{ display: "block", fontSize: "0.75rem", color: "var(--text-soft)" }}>{Object.keys(team.procedure.roles).join(" · ")}</span>
                  </span>
                </label>
              );
            })}
          </fieldset>
          <Field label="Workspace name">
            <input
              style={inputStyle}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && create()}
              placeholder={selected?.procedure.name ?? "e.g. payments"}
              disabled={!selected}
            />
          </Field>
          <Field label="Folder the team works in">
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                style={{ ...inputStyle, flex: 1 }}
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="a git repository on this Mac"
                disabled={!selected}
              />
              <Button size="sm" variant="ghost" onClick={() => setBrowsing(true)} disabled={!selected}>Browse…</Button>
            </div>
          </Field>
          {browsing && (
            <DirBrowser browse={api.browse} start={path || undefined} pickLabel="Use this folder" onPick={(r) => { setPath(r.path); setBrowsing(false); }} onCancel={() => setBrowsing(false)} />
          )}
          <p className="rd-modal-hint" style={{ marginTop: -6 }}>A git repository, not your home folder: the MetaHarness is installed into it and the sessions run there.</p>
          <label className="rd-modal-hint" style={{ display: "flex", gap: 8, alignItems: "flex-start", cursor: prefs?.effective ? "pointer" : "default" }}>
            <input type="checkbox" checked={willStart} disabled={!prefs?.effective} onChange={(e) => setStartSessions(e.target.checked)} style={{ marginTop: 3 }} />
            <span>
              {prefs === null && "Checking which coding harness is installed…"}
              {prefs && prefs.effective && <>Start one session per role now, on <strong>{prefs.effective}</strong>{prefs.default_harness ? "" : " (automatic; the default harness is in Settings)"}. The MetaHarness is installed in the folder first; each role card can add another session later.</>}
              {prefs && !prefs.effective && "No coding harness is installed on this Mac, so no session can start; the workspace and its role cards are still created."}
            </span>
          </label>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
            <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button onClick={create} disabled={busy || !selected || !name.trim() || !path.trim()}>{willStart && roles ? `Create team · ${roles} session${roles === 1 ? "" : "s"}` : "Create team"}</Button>
          </div>
        </>
      )}
    </Modal>
  );
}

function key(team: Offer) {
  return `${team.kit.name}/${team.procedure.name}`;
}
