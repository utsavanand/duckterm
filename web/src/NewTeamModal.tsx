import { useEffect, useState } from "react";
import { api, KitEntry, KitProcedure } from "./api";
import { teamRequest } from "./teams";
import { Button, Field, inputStyle, Modal, useToast } from "./ui";

// New ▾ › New team: the team templates every added MetaHarness offers, and the
// workspace they become. A second door to the Create team button on the
// MetaHarness card; both send the same request.
export function NewTeamModal({
  sessionKey,
  existing,
  onClose,
  onOpenKits,
  onCreated,
}: {
  sessionKey?: string;
  existing: string[];
  onClose: () => void;
  onOpenKits: () => void;
  onCreated: (folder: string) => void;
}) {
  const toast = useToast();
  const [teams, setTeams] = useState<{ kit: KitEntry; procedure: KitProcedure }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api.kitsCatalog(sessionKey).then(
      (catalog) => {
        if (!active) return;
        const offered = catalog.kits.flatMap((kit) => (kit.procedures ?? []).map((procedure) => ({ kit, procedure })));
        setTeams(offered);
        if (offered.length === 1) {
          setChosen(key(offered[0]));
          setName(offered[0].procedure.name);
        }
      },
      (e: Error) => { if (active) setError(e.message); },
    );
    return () => { active = false; };
  }, [sessionKey]);

  const selected = teams?.find((t) => key(t) === chosen) ?? null;

  function choose(team: { kit: KitEntry; procedure: KitProcedure }) {
    setChosen(key(team));
    if (!name.trim() || name === selected?.procedure.name) setName(team.procedure.name);
  }

  async function create() {
    const trimmed = name.trim();
    if (!selected || !trimmed) return;
    if (existing.includes(trimmed)) {
      toast(`Workspace "${trimmed}" already exists`, "err");
      return;
    }
    setBusy(true);
    try {
      const out = await api.kitsCreateTeam(teamRequest(selected.kit, selected.procedure.name, trimmed), sessionKey);
      toast(`Team ${out.team.team}: workspace "${out.team.folder}" created with ${Object.keys(out.team.roles).length} roles`, "ok");
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
          <p className="rd-modal-hint">Creates the workspace with one card per role; the MetaHarness installs when its first session starts.</p>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button onClick={create} disabled={busy || !selected || !name.trim()}>Create team</Button>
          </div>
        </>
      )}
    </Modal>
  );
}

function key(team: { kit: KitEntry; procedure: KitProcedure }) {
  return `${team.kit.name}/${team.procedure.name}`;
}
