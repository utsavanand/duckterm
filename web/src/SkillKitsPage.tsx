import { useCallback, useEffect, useState } from "react";
import { api, KitCatalog, KitDetail, KitEnableResult, KitEntry, KitFix, KitPlanRow, KitProcedure, KitReview, KitWorkspace } from "./api";
import { DirBrowser } from "./DirBrowser";
import { Button, useToast } from "./ui";
import "./skillKits.css";

// The Skill kits page (Settings › Skill kits): the kits added to DuckTerm,
// what each holds, and the workspaces each is enabled for. Adding is three
// steps: pick the kit folder, read what it holds, enable it for all
// workspaces or chosen ones. Enabling installs into every matching
// repository known now, for every harness, and a workspace with no session
// yet gets the kit when its first session starts.

type View =
  | { name: "list" }
  | { name: "add" }
  | { name: "kit"; kit: string; fromAdd?: boolean }
  | { name: "enable"; kit: string; fromAdd?: boolean; error?: string }
  | { name: "review"; kit: string; fromAdd?: boolean; scope: "all" | "workspaces"; workspaces: string[]; options: Record<string, string>; fallbacks: string[]; replace: boolean; review: KitReview }
  | { name: "done"; kit: string; result: KitEnableResult };

const STATUS_CLASS: Record<string, string> = {
  installed: "ok",
  checked: "ok",
  offered: "",
  "installed, needs trust": "warn",
  "installed, not yet verified": "warn",
  "not supported": "bad",
  missing: "bad",
  "not applicable": "mute",
};

const LABEL = { display: "block", fontSize: "0.6875rem", letterSpacing: "0.06em", textTransform: "uppercase" as const, color: "var(--text-soft)", marginBottom: 6 };

function Pill({ children, tone }: { children: React.ReactNode; tone?: string }) {
  return <span className={`rd-kit-pill${tone ? ` ${tone}` : ""}`}>{children}</span>;
}

function StatusPill({ status }: { status: string }) {
  return <Pill tone={STATUS_CLASS[status] ?? ""}>{status}</Pill>;
}

function workspaceLabel(name: string): string {
  return name || "Ungrouped";
}

function policyText(kit: KitEntry): string {
  if (!kit.policy) return "not enabled for any workspace yet";
  if (kit.policy.scope === "all") return "enabled for all workspaces";
  const names = kit.policy.workspaces ?? [];
  return names.length ? `enabled for ${names.map(workspaceLabel).join(", ")}` : "enabled for no workspace";
}

function describeRule(rule: Record<string, unknown>): string {
  const role = String(rule.role ?? "");
  if (typeof rule.review_by === "string") return `${role}'s work is reviewed by ${rule.review_by}`;
  if (Array.isArray(rule.may_not)) return `${role} may not ${rule.may_not.join(", ")}`;
  if (typeof rule.carries === "string") return `${role} carries ${rule.carries}`;
  if (typeof rule.must_reply_to === "string") return `${role} must reply to ${rule.must_reply_to}${rule.within ? ` within ${rule.within}` : ""}`;
  return Object.entries(rule).filter(([k]) => k !== "role").map(([k, v]) => `${k}: ${String(v)}`).join(", ");
}

export function SkillKitsPage({
  sessionKey = "",
  onClose,
  fallback,
  onTeamCreated,
}: {
  sessionKey?: string;
  onClose: () => void;
  fallback: React.ReactNode;
  onTeamCreated?: (folder: string) => void;
}) {
  const toast = useToast();
  const [catalog, setCatalog] = useState<KitCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [view, setView] = useState<View>({ name: "list" });
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await api.kitsCatalog(sessionKey);
      setCatalog(next);
      setError(null);
      return next;
    } catch (e) {
      const message = (e as Error).message;
      if (/still in development|unavailable in Duckterm/i.test(message)) { setUnavailable(true); return null; }
      setError(message);
      return null;
    }
  }, [sessionKey]);

  useEffect(() => { refresh(); }, [refresh]);

  if (unavailable) return <>{fallback}</>;

  const entry = (name: string) => catalog?.kits.find((k) => k.name === name) ?? null;

  async function add(path: string) {
    setBusy(true);
    try {
      const out = await api.kitsRegister(path, sessionKey);
      toast(`Added ${out.name} ${out.version}`, "ok");
      await refresh();
      setView({ name: "kit", kit: out.name, fromAdd: true });
    } catch (e) {
      toast(`Could not add kit: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    setBusy(true);
    try {
      await api.kitsUnregister(name, sessionKey);
      toast(`${name} removed from DuckTerm; its installs stay in their repositories`, "ok");
      await refresh();
      setView({ name: "list" });
    } catch (e) {
      toast(`Could not remove: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  async function review(kit: KitEntry, scope: "all" | "workspaces", workspaces: string[], options: Record<string, string>, fromAdd?: boolean, fallbacks: string[] = [], replace = false) {
    setBusy(true);
    setView({ name: "enable", kit: kit.name, fromAdd });
    try {
      const review = await api.kitsReview({ kit: kit.name, scope, workspaces: scope === "workspaces" ? workspaces : undefined, options, fallbacks }, sessionKey);
      setView({ name: "review", kit: kit.name, fromAdd, scope, workspaces, options, fallbacks, replace, review });
    } catch (e) {
      // The dry run stops at the first repository the server refuses (one
      // deleted since the page listed it, say); the wizard shows which and
      // why and keeps the choices, so the owner can go back or pick again.
      setView({ name: "enable", kit: kit.name, fromAdd, error: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function applyFix(kit: KitEntry, view: Extract<View, { name: "review" }>, fix: KitFix) {
    if (fix.kind === "replace") {
      setView({ ...view, replace: true });
      return;
    }
    const fallbacks = fix.kind === "fallback" ? Array.from(new Set([...view.fallbacks, fix.component])) : view.fallbacks;
    if (fix.kind === "codex-trust") {
      setBusy(true);
      try {
        await api.kitsFix({ kind: "codex-trust", projects: fix.projects }, sessionKey);
      } catch (e) {
        toast(`Could not trust in Codex: ${(e as Error).message}`, "err");
        setBusy(false);
        return;
      }
    }
    await review(kit, view.scope, view.workspaces, view.options, view.fromAdd, fallbacks, view.replace);
  }

  async function enable(kit: KitEntry, scope: "all" | "workspaces", workspaces: string[], options: Record<string, string>, fallbacks: string[], replace: boolean) {
    setBusy(true);
    try {
      const result = await api.kitsEnable({ kit: kit.name, scope, workspaces: scope === "workspaces" ? workspaces : undefined, options, fallbacks, replace }, sessionKey);
      await refresh();
      setView({ name: "done", kit: kit.name, result });
    } catch (e) {
      toast(`Enable refused: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  async function disable(kit: KitEntry) {
    setBusy(true);
    try {
      await api.kitsDisable(kit.name, sessionKey);
      toast(`${kit.name} is no longer enabled for new sessions; installed repositories keep it`, "ok");
      await refresh();
    } catch (e) {
      toast(`Could not disable: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  async function uninstall(name: string, project: string) {
    setBusy(true);
    try {
      const out = await api.kitsUninstall(project, name, sessionKey);
      toast(`Uninstalled ${name}: ${out.report.removed.length} removed, ${out.report.kept.length} kept`, "ok");
      await refresh();
    } catch (e) {
      toast(`Uninstall refused: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  async function verify(project: string) {
    try {
      const out = await api.kitsVerify(project, sessionKey);
      const drift = out.reports.filter((r) => r.missing.length || r.drifted.length);
      toast(drift.length ? `${drift.length} kit(s) drifted in ${project}` : "verified", drift.length ? "err" : "ok");
      await refresh();
    } catch (e) {
      toast(`Verify failed: ${(e as Error).message}`, "err");
    }
  }

  async function createTeam(kit: KitEntry, team: string, name: string) {
    setBusy(true);
    try {
      const project = kit.installed_in[0]?.project ?? null;
      const out = await api.kitsCreateTeam({ project, kit_path: kit.path, kit: kit.name, team, name }, sessionKey);
      toast(`Team ${out.team.team}: workspace "${out.team.folder}" created with ${Object.keys(out.team.roles).length} roles`, "ok");
      onTeamCreated?.(out.team.folder);
    } catch (e) {
      toast(`Create team refused: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  const current = view.name !== "list" && view.name !== "add" ? entry(view.kit) : null;
  const crumb = (
    <div className="rd-kits-crumb">
      <button onClick={onClose}>Settings</button><span>›</span>
      {view.name === "list" ? <span>Skill kits</span> : <button onClick={() => setView({ name: "list" })}>Skill kits</button>}
      {view.name === "add" && <><span>›</span><span>Add</span></>}
      {view.name === "kit" && <><span>›</span><span>{view.kit}</span></>}
      {(view.name === "enable" || view.name === "review") && <><span>›</span><button onClick={() => setView({ name: "kit", kit: view.kit })}>{view.kit}</button><span>›</span><span>Enable</span></>}
      {view.name === "done" && <><span>›</span><button onClick={() => setView({ name: "kit", kit: view.kit })}>{view.kit}</button><span>›</span><span>Enabled</span></>}
    </div>
  );

  return (
    <div className="rd-kits-page" role="dialog" aria-label="Skill kits">
      <div className="rd-kits-main">
        {crumb}
        {view.name === "list" && (
          <ListView
            catalog={catalog}
            error={error}
            busy={busy}
            onAdd={() => setView({ name: "add" })}
            onOpen={(name) => setView({ name: "kit", kit: name })}
            onClose={onClose}
            onCreateTeam={createTeam}
          />
        )}
        {view.name === "add" && <AddView busy={busy} onCancel={() => setView({ name: "list" })} onAdd={add} />}
        {view.name === "kit" && current && (
          <KitView
            kit={current}
            busy={busy}
            fromAdd={!!view.fromAdd}
            onEnable={() => setView({ name: "enable", kit: current.name, fromAdd: view.fromAdd })}
            onDisable={() => disable(current)}
            onRemove={() => remove(current.name)}
            onVerify={verify}
            onUninstall={(project) => uninstall(current.name, project)}
            onCreateTeam={createTeam}
            onDone={() => setView({ name: "list" })}
          />
        )}
        {view.name === "kit" && !current && <div className="rd-kits-error">{view.kit} is no longer added.</div>}
        {view.name === "enable" && current && (
          <EnableView
            kit={current}
            workspaces={catalog?.workspaces ?? []}
            busy={busy}
            fromAdd={!!view.fromAdd}
            error={view.error}
            onBack={() => setView({ name: "kit", kit: current.name, fromAdd: view.fromAdd })}
            onReview={(scope, workspaces, options) => review(current, scope, workspaces, options, view.fromAdd)}
          />
        )}
        {view.name === "review" && current && (
          <ReviewView
            kit={current}
            view={view}
            busy={busy}
            onBack={() => setView({ name: "enable", kit: current.name, fromAdd: view.fromAdd })}
            onFix={(fix) => applyFix(current, view, fix)}
            onEnable={() => enable(current, view.scope, view.workspaces, view.options, view.fallbacks, view.replace)}
          />
        )}
        {view.name === "done" && current && (
          <DoneView kit={current} result={view.result} onOpen={() => setView({ name: "kit", kit: current.name })} onDone={() => setView({ name: "list" })} />
        )}
      </div>
    </div>
  );
}

function kitState(kit: KitEntry): { text: string; tone: string } {
  if (kit.error) return { text: "cannot be read", tone: "bad" };
  const errors = kit.inventory?.findings.filter((f) => f.level === "error").length ?? 0;
  if (errors) return { text: `${errors} manifest error${errors === 1 ? "" : "s"}`, tone: "bad" };
  if (kit.changed) return { text: "changed since enabled", tone: "warn" };
  if (kit.installed_in.length === 0) return kit.policy ? { text: "waiting for a first session", tone: "warn" } : { text: "not enabled", tone: "mute" };
  const drifted = kit.installed_in.filter((i) => i.verify.ok === false).length;
  if (drifted) return { text: `drift in ${drifted} of ${kit.installed_in.length}`, tone: "bad" };
  const unchecked = kit.installed_in.filter((i) => i.verify.ok === null).length;
  if (unchecked) return { text: "installed, not yet verified", tone: "warn" };
  return { text: "verified", tone: "ok" };
}

function ListView({ catalog, error, busy, onAdd, onOpen, onClose, onCreateTeam }: {
  catalog: KitCatalog | null; error: string | null; busy: boolean;
  onAdd: () => void; onOpen: (name: string) => void; onClose: () => void;
  onCreateTeam: (kit: KitEntry, team: string, name: string) => void;
}) {
  const kits = catalog?.kits ?? [];
  const teams = kits.flatMap((k) => (k.procedures ?? []).map((p) => ({ kit: k, procedure: p })));
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>Skill kits</h1>
          <p>Kits added to DuckTerm and the workspaces they are enabled for. A kit teaches every harness how a team works: knowledge, skills, agents, hooks, permissions, tools, flows, and sometimes a procedure.</p>
        </div>
        <div className="rd-kits-actions">
          <Button onClick={onAdd}>Add skill kit…</Button>
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </div>
      </div>
      {error && <div className="rd-kits-error">{error}</div>}
      <section className="rd-kits-section">
        <h2>Kits <span>{catalog ? `${kits.length} added` : ""}</span></h2>
        <div className="rd-kits-table">
          <div className="rd-kits-row"><span>Kit</span><span>Enabled for</span><span>State</span><span /></div>
          {kits.map((k) => {
            const state = kitState(k);
            return (
              <div className="rd-kits-row" key={k.name}>
                <div>
                  <div className="rd-kits-name">{k.name} <span className="rd-kits-mono" style={{ fontWeight: 400, color: "var(--text-soft)" }}>{k.version ?? ""}</span></div>
                  <div className="rd-kits-sub">{k.error ?? k.description ?? ""}</div>
                </div>
                <div className="rd-kits-pills">
                  {k.policy?.scope === "all" && <Pill>all workspaces</Pill>}
                  {k.policy?.scope === "workspaces" && (k.policy.workspaces ?? []).map((w) => <Pill key={w}>{workspaceLabel(w)}</Pill>)}
                  {!k.policy && <span className="rd-kits-sub">nowhere yet</span>}
                </div>
                <div className="rd-kits-pills"><Pill tone={state.tone}>{state.text}</Pill>{(k.flows ?? []).length > 0 && <Pill>{k.flows!.length} flow{k.flows!.length === 1 ? "" : "s"}</Pill>}</div>
                <Button variant="ghost" size="sm" onClick={() => onOpen(k.name)}>Open</Button>
              </div>
            );
          })}
          {catalog && kits.length === 0 && <div className="rd-kits-row" style={{ gridTemplateColumns: "1fr" }}><span className="rd-kits-sub">No kit is added yet. Add one from a folder on this machine, then enable it for your workspaces.</span></div>}
        </div>
      </section>
      <section className="rd-kits-section">
        <h2>Teams these kits offer <span>a team is a workspace with a procedure; creating one makes a new workspace</span></h2>
        <div className="rd-kits-table">
          <div className="rd-kits-prow"><span>Team</span><span>Roles and rules</span><span /></div>
          {teams.map(({ kit, procedure }) => (
            <ProcedureRow key={`${kit.name}/${procedure.name}`} procedure={procedure} kit={kit} busy={busy} onCreate={(name) => onCreateTeam(kit, procedure.name, name)} />
          ))}
          {catalog && teams.length === 0 && <div className="rd-kits-prow" style={{ gridTemplateColumns: "1fr" }}><span className="rd-kits-sub">None of the added kits offers a team.</span></div>}
        </div>
      </section>
    </>
  );
}

function ProcedureRow({ procedure, kit, busy, onCreate }: { procedure: KitProcedure; kit: KitEntry; busy: boolean; onCreate: (name: string) => void }) {
  return (
    <div className="rd-kits-prow">
      <div>
        <div className="rd-kits-name">{procedure.name} <span className="rd-kits-mono" style={{ fontWeight: 400, color: "var(--text-soft)" }}>from {kit.name}</span></div>
        <div className="rd-kits-sub">{Object.keys(procedure.roles).join(" · ")}</div>
      </div>
      <div style={{ fontSize: "0.75rem" }}>{procedure.rules.map(describeRule).join(" · ") || procedure.description}</div>
      <CreateTeam name={procedure.name} busy={busy} onCreate={onCreate} />
    </div>
  );
}

function CreateTeam({ name, busy, onCreate }: { name: string; busy: boolean; onCreate: (name: string) => void }) {
  const [asking, setAsking] = useState(false);
  const [value, setValue] = useState(name);
  if (!asking) {
    return (
      <div className="rd-kits-pills" style={{ justifyContent: "flex-end" }}>
        <Button size="sm" onClick={() => setAsking(true)} disabled={busy}>Create team</Button>
      </div>
    );
  }
  return (
    <form className="rd-kits-actions" style={{ justifyContent: "flex-end", alignItems: "center" }} onSubmit={(e) => { e.preventDefault(); if (value.trim()) onCreate(value.trim()); setAsking(false); }}>
      <input type="text" aria-label="Workspace name" value={value} onChange={(e) => setValue(e.target.value)} style={{ width: 200 }} autoFocus />
      <Button size="sm" disabled={busy || !value.trim()} onClick={() => { if (value.trim()) { onCreate(value.trim()); setAsking(false); } }}>Create workspace</Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setAsking(false)}>Cancel</Button>
    </form>
  );
}

function AddView({ busy, onCancel, onAdd }: { busy: boolean; onCancel: () => void; onAdd: (path: string) => void }) {
  const [path, setPath] = useState("");
  const [browsing, setBrowsing] = useState(false);
  return (
    <>
      <div className="rd-kits-head">
        <h1>Add skill kit</h1>
        <span className="rd-kits-sub">Step 1 of 3 · pick the kit; nothing is installed yet</span>
      </div>
      <form className="rd-kits-form" style={{ gridTemplateColumns: "1fr" }} onSubmit={(e) => { e.preventDefault(); if (!browsing && path.trim()) onAdd(path.trim()); }}>
        <div>
          <label htmlFor="rd-kits-kit" style={LABEL}>Kit folder</label>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input id="rd-kits-kit" type="text" value={path} onChange={(e) => setPath(e.target.value)} placeholder="~/Downloads/uv-suite" autoFocus style={{ flex: 1 }} />
            <Button type="button" variant="ghost" onClick={() => setBrowsing((b) => !b)}>Browse…</Button>
          </div>
          <div className="rd-kits-note">The folder a kit was downloaded or cloned into; it holds skill-kit.toml. When one folder holds several kits, Browse offers each by name. Only the manifest is read; no kit code runs.</div>
          {browsing && (
            <DirBrowser
              browse={api.browse}
              start={path.trim() || undefined}
              kitsOnly
              pickLabel="Use this kit folder"
              onPick={(r) => { setPath(r.path); setBrowsing(false); }}
              onCancel={() => setBrowsing(false)}
            />
          )}
        </div>
      </form>
      <div className="rd-kits-footer">
        <span />
        <div className="rd-kits-actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button type="button" onClick={() => { if (path.trim()) onAdd(path.trim()); }} disabled={busy || !path.trim()}>Add</Button>
        </div>
      </div>
    </>
  );
}

function KitView({ kit, busy, fromAdd, onEnable, onDisable, onRemove, onVerify, onUninstall, onCreateTeam, onDone }: {
  kit: KitEntry; busy: boolean; fromAdd: boolean; onEnable: () => void; onDisable: () => void; onRemove: () => void;
  onVerify: (project: string) => void; onUninstall: (project: string) => void;
  onCreateTeam: (kit: KitEntry, team: string, name: string) => void; onDone: () => void;
}) {
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [confirmUninstall, setConfirmUninstall] = useState<string | null>(null);
  const details = kit.details ?? {};
  const flows = details.flow ?? (kit.flows ?? []).map((f) => ({ name: f }));
  const runs = kit.runs ?? [];
  const lastRun = (flow: string) => [...runs].sort((a, b) => (a.started_at ?? 0) - (b.started_at ?? 0)).reverse().find((r) => r.flow === `${kit.name}/${flow}`);
  const errors = kit.inventory?.findings.filter((f) => f.level === "error") ?? [];
  const warnings = (kit.inventory?.findings.length ?? 0) - errors.length;
  const groups: [string, string][] = [["skill", "Skills"], ["agent", "Agents"], ["knowledge", "Knowledge"], ["hook", "Hooks"], ["permission", "Permissions"], ["tool", "Tools"], ["settings", "Settings"]];
  const canEnable = !busy && !kit.error && errors.length === 0;
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>{kit.name} <span className="rd-kits-mono" style={{ fontWeight: 400, color: "var(--text-soft)" }}>{kit.version ?? ""}</span></h1>
          <p>{kit.error ?? kit.description} · <span className="rd-kits-mono">{kit.path}</span></p>
          {fromAdd && <p className="rd-kits-sub">Step 2 of 3 · what the kit holds. Next, choose the workspaces it is for.</p>}
        </div>
        <div className="rd-kits-actions">
          {!fromAdd && <Button onClick={onEnable} disabled={!canEnable}>{kit.policy ? "Change workspaces…" : "Enable for workspaces…"}</Button>}
          {!fromAdd && kit.policy && <Button variant="ghost" onClick={onDisable} disabled={busy}>Disable</Button>}
          {!fromAdd && (!confirmRemove ? <Button variant="ghost" onClick={() => setConfirmRemove(true)}>Remove from DuckTerm…</Button> : <Button variant="danger" onClick={onRemove} disabled={busy}>Confirm remove</Button>)}
        </div>
      </div>
      {confirmRemove && <div className="rd-kits-note">Forgets the kit here. Repositories it is installed in keep their files; uninstall those first if you want them gone.</div>}
      {kit.error && <div className="rd-kits-error">{kit.error}</div>}
      <div className="rd-kits-pills">
        <Pill tone={kit.policy ? "ok" : "mute"}>{policyText(kit)}</Pill>
        {(kit.pending ?? []).length > 0 && <Pill tone="warn">{kit.pending!.map(workspaceLabel).join(", ")}: installs at the first session</Pill>}
        {kit.changed && <Pill tone="warn">the kit folder changed since it was enabled: nothing new installs until you enable it again</Pill>}
        {kit.inventory && <Pill tone={errors.length ? "bad" : "ok"}>{errors.length ? `${errors.length} manifest errors` : "validate ok"}{warnings ? ` · ${warnings} warnings` : ""}</Pill>}
        {kit.inventory && <Pill>{kit.inventory.component_count} components</Pill>}
        {kit.inventory && Object.keys(kit.inventory.options).length > 0 && <Pill>options: {Object.keys(kit.inventory.options).join(", ")}</Pill>}
      </div>
      {errors.map((f) => <div key={f.component + f.message} className="rd-kits-error">{f.component}: {f.message}</div>)}
      {(kit.procedures ?? []).map((p) => (
        <div className="rd-kits-card" key={p.name} style={{ borderColor: "var(--brand-green)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}><strong>Team: {p.name}</strong><CreateTeam name={p.name} busy={busy} onCreate={(name) => onCreateTeam(kit, p.name, name)} /></div>
          <div style={{ fontSize: "0.75rem" }}>{Object.keys(p.roles).join(" · ")} · {p.rules.map(describeRule).join(" · ")}</div>
          <div className="rd-kits-note">Creating the team makes a new workspace with these roles and enables this kit for it.</div>
        </div>
      ))}
      {flows.length > 0 && (
        <div className="rd-kits-card">
          <div className="rd-flow-head"><strong>Flows</strong><span>Trigger</span><span>Shortcut</span><span>Last run</span></div>
          {flows.map((f) => {
            const last = lastRun(f.name);
            return (
              <div className="rd-flow" key={f.name}>
                <div><span className="rd-kits-mono">{f.name}</span>{f.description && <div className="rd-kits-note">{f.description}</div>}</div>
                <span style={{ color: "var(--text-soft)" }}>{f.trigger ?? "none"}</span>
                <span className="rd-kits-mono">/{f.name}{(f.inputs ?? []).map((i) => ` <${i}>`).join("")}</span>
                <span>{last ? <Pill tone={last.state === "succeeded" ? "ok" : last.state === "failed" ? "bad" : last.state === "waiting_gate" ? "warn" : ""}>{last.state === "waiting_gate" ? "waiting for you" : last.state}</Pill> : <span style={{ color: "var(--text-soft)" }}>never</span>}</span>
              </div>
            );
          })}
        </div>
      )}
      <div className="rd-kits-sections">
        {groups.map(([kind, title]) => {
          const items = details[kind] ?? [];
          if (items.length === 0) return null;
          return (
            <details className="rd-kits-group" key={kind}>
              <summary><strong>{title}</strong><span className="rd-kits-sub">{kind === "permission" ? items.reduce((n, i) => n + (i.rules?.length ?? 0), 0) : items.length}</span></summary>
              {items.map((d) => <DetailRow key={d.name} kind={kind} d={d} />)}
            </details>
          );
        })}
      </div>
      {!fromAdd && (
        <section className="rd-kits-section">
          <h2>Installed in <span>{kit.installed_in.length ? `${kit.installed_in.length} repositor${kit.installed_in.length === 1 ? "y" : "ies"}` : "no repository yet"}</span></h2>
          <div className="rd-kits-table">
            <div className="rd-kits-row"><span>Workspace</span><span>Harnesses</span><span>State</span><span /></div>
            {kit.installed_in.map((i) => (
              <div className="rd-kits-row" key={i.project}>
                <div>
                  <div className="rd-kits-name">{workspaceLabel(i.workspace)}</div>
                  <div className="rd-kits-sub rd-kits-mono">{i.project}</div>
                </div>
                <div className="rd-kits-pills">{i.harnesses.map((h) => <Pill key={h}>{h}</Pill>)}</div>
                <div className="rd-kits-pills">
                  {i.verify.ok === true && <Pill tone="ok">verified</Pill>}
                  {i.verify.ok === false && <Pill tone="bad">drift: {i.verify.missing.length} missing, {i.verify.drifted.length} drifted</Pill>}
                  {i.verify.ok === null && <Pill>not verified</Pill>}
                  {i.version && i.version !== kit.version && <Pill tone="warn">installed {i.version}</Pill>}
                  {!i.trusted && <Pill tone="warn">record written on another machine</Pill>}
                </div>
                <div className="rd-kits-actions" style={{ justifyContent: "flex-end" }}>
                  <Button variant="ghost" size="sm" onClick={() => onVerify(i.project)}>Verify</Button>
                  {confirmUninstall !== i.project
                    ? <Button variant="ghost" size="sm" onClick={() => setConfirmUninstall(i.project)}>Uninstall…</Button>
                    : <Button variant="danger" size="sm" disabled={busy} onClick={() => { onUninstall(i.project); setConfirmUninstall(null); }}>Confirm</Button>}
                </div>
              </div>
            ))}
            {kit.installed_in.length === 0 && <div className="rd-kits-row" style={{ gridTemplateColumns: "1fr" }}><span className="rd-kits-sub">{kit.policy ? "Enabled, but no matching workspace has run a session yet. The first session installs it." : "Not installed in any repository. Enable the kit for workspaces to install it."}</span></div>}
          </div>
          {confirmUninstall && <div className="rd-kits-note">Removes exactly what the install record lists and restores every edited file byte for byte when you have not touched it since.</div>}
        </section>
      )}
      {fromAdd && (
        <div className="rd-kits-footer">
          <span />
          <div className="rd-kits-actions">
            <Button variant="ghost" onClick={onDone}>Finish without enabling</Button>
            <Button onClick={onEnable} disabled={!canEnable}>Next: choose workspaces</Button>
          </div>
        </div>
      )}
    </>
  );
}

function EnableView({ kit, workspaces, busy, fromAdd, error, onBack, onReview }: {
  kit: KitEntry; workspaces: KitWorkspace[]; busy: boolean; fromAdd: boolean; error?: string; onBack: () => void;
  onReview: (scope: "all" | "workspaces", workspaces: string[], options: Record<string, string>) => void;
}) {
  const [scope, setScope] = useState<"all" | "workspaces">(kit.policy?.scope ?? "all");
  const [chosen, setChosen] = useState<Set<string>>(new Set(kit.policy?.workspaces ?? []));
  const optionSpecs = kit.inventory?.options ?? {};
  const [options, setOptions] = useState<Record<string, string>>(kit.policy?.options ?? Object.fromEntries(Object.entries(optionSpecs).flatMap(([n, s]) => (s.default ? [[n, s.default]] : []))));
  const toggle = (name: string) => setChosen((s) => { const next = new Set(s); if (next.has(name)) next.delete(name); else next.add(name); return next; });
  const names = Array.from(chosen);
  const ready = scope === "all" || names.length > 0;
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>Enable {kit.name} for…</h1>
          <p>{fromAdd ? "Step 3 of 3 · " : ""}A workspace's sessions get the kit wherever they run, for every harness: its repositories known now at Enable, any other folder when a session starts in it.</p>
        </div>
      </div>
      <div className="rd-kits-table">
        <label className="rd-kits-row rd-kits-check" style={{ gridTemplateColumns: "1fr", cursor: "pointer" }}>
          <span><input type="radio" name="rd-kits-scope" aria-label="All workspaces" checked={scope === "all"} onChange={() => setScope("all")} /> <strong>All workspaces</strong> <span className="rd-kits-sub">every workspace now and every one you create later</span></span>
        </label>
        <label className="rd-kits-row rd-kits-check" style={{ gridTemplateColumns: "1fr", cursor: "pointer" }}>
          <span><input type="radio" name="rd-kits-scope" aria-label="These workspaces" checked={scope === "workspaces"} onChange={() => setScope("workspaces")} /> <strong>These workspaces</strong></span>
        </label>
        {scope === "workspaces" && workspaces.length > 0 && (
          <div className="rd-kits-workspaces">
            {workspaces.map((w) => (
              <label className="rd-kits-check" key={w.name || "(ungrouped)"}>
                <input type="checkbox" checked={chosen.has(w.name)} onChange={() => toggle(w.name)} aria-label={workspaceLabel(w.name)} />
                <strong>{workspaceLabel(w.name)}</strong>
                {w.repositories.length === 0 && <span className="rd-kits-sub">no session yet</span>}
              </label>
            ))}
          </div>
        )}
        {scope === "workspaces" && workspaces.length === 0 && <div className="rd-kits-row" style={{ gridTemplateColumns: "1fr" }}><span className="rd-kits-sub">No workspace yet. Choose All, or make one with New › New workspace and come back.</span></div>}
      </div>
      {Object.entries(optionSpecs).map(([name, spec]) => (
        <div key={name} style={{ maxWidth: 420 }}>
          <label htmlFor={`rd-kits-opt-${name}`} style={LABEL}>{name}</label>
          <select id={`rd-kits-opt-${name}`} value={options[name] ?? spec.default ?? ""} onChange={(e) => setOptions({ ...options, [name]: e.target.value })}>
            {(spec.choices ?? []).map((c) => <option key={c} value={c}>{c}{c === spec.default ? " (default)" : ""}</option>)}
          </select>
          <div className="rd-kits-note">{spec.description ?? "A kit option: each choice turns components on or off."}</div>
        </div>
      ))}
      {error && (
        <div className="rd-kits-error" role="alert">
          <strong>The dry run could not be read.</strong> {error}
          <div className="rd-kits-note" style={{ color: "inherit" }}>Nothing was written. Choose other workspaces and review again, or go back.</div>
        </div>
      )}
      <div className="rd-kits-footer">
        <span>{ready ? `${scope === "all" ? "Every workspace" : `${names.length} workspace${names.length === 1 ? "" : "s"}`} chosen. The review shows what every harness gets and what needs a fix.` : "Choose at least one workspace."}</span>
        <div className="rd-kits-actions">
          <Button variant="ghost" onClick={onBack}>Back</Button>
          <Button onClick={() => onReview(scope, names, options)} disabled={busy || !ready}>Review what will change</Button>
        </div>
      </div>
    </>
  );
}

const KIND_TITLES: Record<string, string> = { knowledge: "Knowledge", skill: "Skills", agent: "Agents", hook: "Hooks", permission: "Permissions", tool: "Tools", flow: "Flows", settings: "Settings", team: "Procedures" };
const KIND_ORDER = ["knowledge", "skill", "agent", "hook", "permission", "tool", "flow", "settings", "team"];

function describeFix(fix: KitFix): string {
  if (fix.kind === "codex-trust") return `Fix · trust ${fix.projects.length === 1 ? "it" : `all ${fix.projects.length}`} in Codex`;
  if (fix.kind === "fallback") return `Fix · load it always on ${fix.harnesses.join(", ")}`;
  return "Fix · replace with the kit's";
}

function ReviewView({ kit, view, busy, onBack, onFix, onEnable }: {
  kit: KitEntry; view: Extract<View, { name: "review" }>; busy: boolean; onBack: () => void; onFix: (fix: KitFix) => void; onEnable: () => void;
}) {
  const { review, replace } = view;
  const plan = review.plan;
  const repos = review.repositories;
  const soft = repos.flatMap((r) => r.conflicts.filter((c) => c.kind === "file-exists").map((c) => ({ ...c, project: r.project, workspace: r.workspace })));
  const hard = repos.flatMap((r) => r.conflicts.filter((c) => c.kind !== "file-exists").map((c) => ({ ...c, project: r.project, workspace: r.workspace })));
  // A manifest error is the kit's, not a repository's: one line however many repositories.
  const errors = Array.from(new Map(plan.findings.filter((f) => f.level === "error").map((f) => [f.component + f.message, f])).values());
  const hooks = kit.inventory?.components.hook?.length ?? 0;
  const untrusted = repos.filter((r) => r.codex_trusted === false);
  const trustFix = review.fixes.find((f): f is Extract<KitFix, { kind: "codex-trust" }> => f.kind === "codex-trust");
  const fallbackFixes = review.fixes.filter((f): f is Extract<KitFix, { kind: "fallback" }> => f.kind === "fallback");
  const unsupported = plan.harnesses.map((h) => ({ harness: h, count: plan.rows.filter((r) => r.harnesses[h] === "not supported").length })).filter((x) => x.count > 0);
  const workspaceNames = Array.from(new Set(repos.map((r) => workspaceLabel(r.workspace))));
  const attention = untrusted.length + fallbackFixes.length + soft.length + hard.length + unsupported.reduce((n, u) => n + u.count, 0) - fallbackFixes.reduce((n, f) => n + f.harnesses.length, 0);
  const fixable = (trustFix ? 1 : 0) + fallbackFixes.length + (soft.length && !replace ? 1 : 0);
  const byKind = KIND_ORDER.filter((k) => plan.rows.some((r) => r.kind === k)).map((kind) => ({ kind, rows: plan.rows.filter((r) => r.kind === kind) }));
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>Review: {kit.name} {kit.version ?? ""}</h1>
          <p>A dry run; nothing is written until you press Enable</p>
        </div>
        <div className="rd-kits-pills">
          <Pill tone={errors.length ? "bad" : "ok"}>{errors.length ? `${errors.length} error${errors.length === 1 ? "" : "s"}` : "validate ok"}</Pill>
          <Pill tone={hard.length ? "bad" : soft.length && !replace ? "warn" : "ok"}>{soft.length + hard.length} conflict{soft.length + hard.length === 1 ? "" : "s"}</Pill>
        </div>
      </div>

      <div className="rd-kits-card" data-testid="rd-kits-where">
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
          <strong>{workspaceNames.length ? `${workspaceNames.join(", ")} · every harness` : view.scope === "all" ? "Every workspace · every harness" : "No repository yet"}</strong>
          <span className="rd-kits-sub">{repos.length ? `installs now into ${repos.length - repos.filter((r) => r.installed).length} of ${repos.length} repositor${repos.length === 1 ? "y" : "ies"}; ` : ""}any other folder a session starts in gets it then{review.pending.length ? ` (${review.pending.map(workspaceLabel).join(", ")})` : ""}</span>
        </div>
        {untrusted.length > 0 && trustFix && (
          <div className="rd-kits-fixrow">
            <Pill tone="warn">codex</Pill>
            <span>Hooks run only in a repository Codex trusts. {untrusted.length} of {repos.length} {untrusted.length === 1 ? "is" : "are"} not trusted yet; trusting a repository once covers every kit and every session in it.</span>
            <button type="button" className="rd-kits-fix" disabled={busy} onClick={() => onFix(trustFix)}>{describeFix(trustFix)}</button>
          </div>
        )}
        {soft.map((c) => (
          <div className="rd-kits-fixrow" key={c.project + c.path}>
            <Pill tone={replace ? "ok" : "warn"}>conflict</Pill>
            <span><span className="rd-kits-mono">{c.path}</span> {c.detail} in {workspaceLabel(c.workspace)}'s repository; {replace ? "it will be replaced with the kit's (the original is snapshotted first)." : "it is kept and the component skipped there."}</span>
            {!replace && <button type="button" className="rd-kits-fix" disabled={busy} onClick={() => onFix({ kind: "replace", project: c.project, path: c.path })}>Fix · replace with the kit's</button>}
          </div>
        ))}
        {hard.map((c) => (
          <div className="rd-kits-fixrow" key={c.project + c.path + c.kind}>
            <Pill tone="bad">{c.kind}</Pill>
            <span><span className="rd-kits-mono">{c.path || c.component}</span> {c.detail} in {workspaceLabel(c.workspace)}'s repository. A hard conflict refuses the install; there is no override.</span>
          </div>
        ))}
        {errors.map((f) => <div key={f.component + f.message} className="rd-kits-error">{f.component}: {f.message}</div>)}
      </div>

      <div className="rd-kits-card rd-kits-matrix">
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <strong>{plan.rows.length} component{plan.rows.length === 1 ? "" : "s"} · {plan.files.length} file{plan.files.length === 1 ? "" : "s"}</strong>
          <div className="rd-kits-pills">
            {attention > 0 ? <Pill tone="warn">{attention} thing{attention === 1 ? "" : "s"} need{attention === 1 ? "s" : ""} attention</Pill> : <Pill tone="ok">every harness takes everything</Pill>}
            {fixable > 0 && <Pill>{fixable} can be fixed here</Pill>}
          </div>
        </div>
        {byKind.map(({ kind, rows }) => (
          <MatrixSection key={kind} kind={kind} rows={rows} harnesses={plan.harnesses} fixes={fallbackFixes.filter((f) => rows.some((r) => r.component === f.component))} trustAbove={kind === "hook" && untrusted.length > 0} busy={busy} onFix={onFix} />
        ))}
        <details className="rd-kits-sec">
          <summary><strong>Files</strong><span className="rd-kits-sub">{plan.files.length}</span></summary>
          <pre className="rd-kits-files">{plan.files.map((f) => `${f.op.padEnd(13)}${f.path}${f.harness !== "shared" ? ` (${f.harness})` : ""}`).join("\n")}</pre>
          <div className="rd-kits-note">Edited files are snapshotted first; uninstall restores them byte for byte when untouched.</div>
        </details>
      </div>

      <div className="rd-kits-footer">
        <span>No kit script runs during review or install. {hooks} hook script{hooks === 1 ? "" : "s"} run later, inside the harness, through DuckTerm's shim.</span>
        <div className="rd-kits-actions">
          <Button variant="ghost" onClick={onBack}>Back</Button>
          <Button onClick={onEnable} disabled={busy || hard.length > 0 || errors.length > 0}>Enable</Button>
        </div>
      </div>
    </>
  );
}

function MatrixSection({ kind, rows, harnesses, fixes, trustAbove, busy, onFix }: {
  kind: string; rows: KitPlanRow[]; harnesses: string[]; fixes: Extract<KitFix, { kind: "fallback" }>[]; trustAbove: boolean; busy: boolean; onFix: (fix: KitFix) => void;
}) {
  const noteFor = (row: KitPlanRow, h: string) =>
    row.notes.filter((n) => !/^[a-z-]+: /.test(n) || n.startsWith(`${h}: `)).map((n) => n.replace(`${h}: `, "")).join(" · ");
  const unsupported = harnesses.map((h) => ({ harness: h, count: rows.filter((r) => r.harnesses[h] === "not supported").length })).filter((x) => x.count > 0);
  const fixableIds = new Set(fixes.map((f) => f.component));
  const unfixable = unsupported.map((u) => ({ ...u, count: rows.filter((r) => r.harnesses[u.harness] === "not supported" && !fixableIds.has(r.component)).length })).filter((u) => u.count > 0);
  const needsTrust = harnesses.filter((h) => rows.some((r) => r.harnesses[h] === "installed, needs trust"));
  const notApplicable = rows.length > 0 && rows.every((r) => Object.values(r.harnesses).every((s) => s === "not applicable" || s === "installed"));
  const only = rows.length > 0 && harnesses.filter((h) => rows.some((r) => r.harnesses[h] === "installed"));
  const open = fixes.length > 0 || unfixable.length > 0;
  return (
    <details className="rd-kits-sec" open={open}>
      <summary>
        <strong>{KIND_TITLES[kind] ?? kind}</strong><span className="rd-kits-sub">{rows.length}</span>
        <span className="rd-kits-pills" style={{ marginLeft: "auto" }}>
          {fixes.map((f) => <span key={f.component} className="rd-kits-pills"><Pill tone="warn">{f.component.split("/")[2]}: no path scope on {f.harnesses.join(", ")}</Pill><button type="button" className="rd-kits-fix" disabled={busy} onClick={(e) => { e.preventDefault(); onFix(f); }}>{describeFix(f)}</button></span>)}
          {needsTrust.map((h) => <Pill key={h} tone="warn">{h}: needs trust{trustAbove ? " · fix above" : ""}</Pill>)}
          {unfixable.map((u) => <Pill key={u.harness} tone="bad">{u.harness}: {u.count} not supported</Pill>)}
          {unfixable.some((u) => u.harness === "opencode" && kind === "hook") && <Pill tone="mute">no fix yet · plugin planned</Pill>}
          {fixes.length === 0 && unfixable.length === 0 && needsTrust.length === 0 && (kind === "settings" || kind === "team" || (notApplicable && only && only.length < harnesses.length)
            ? <Pill tone="mute">{kind === "team" ? "offered · New team instantiates it" : `${(only || []).join(", ")} only`}</Pill>
            : <Pill tone="ok">installed everywhere</Pill>)}
        </span>
      </summary>
      <div className="rd-kits-plan">
        <table>
          <thead><tr><th style={{ width: "30%" }}>Component</th>{harnesses.map((h) => <th key={h}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((row) => {
              const [, , name] = row.component.split("/");
              return (
                <tr key={row.component}>
                  <td><span className="rd-kits-mono">{name}</span></td>
                  {harnesses.map((h) => (
                    <td key={h}><StatusPill status={row.harnesses[h] ?? "—"} /><div className="rd-kits-note">{noteFor(row, h)}</div></td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function DoneView({ kit, result, onOpen, onDone }: { kit: KitEntry; result: KitEnableResult; onOpen: () => void; onDone: () => void }) {
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>{kit.name} is enabled</h1>
          <p>{result.policy.scope === "all" ? "For all workspaces, now and later." : `For ${(result.policy.workspaces ?? []).map(workspaceLabel).join(", ")}.`}</p>
        </div>
      </div>
      <div className="rd-kits-card">
        {result.installed.length > 0 && <div>Installed into {result.installed.length} repositor{result.installed.length === 1 ? "y" : "ies"}: {result.installed.map((i) => <span key={i.project} className="rd-kits-mono" style={{ display: "block" }}>{workspaceLabel(i.workspace)} · {i.project}</span>)}</div>}
        {result.already.length > 0 && <div className="rd-kits-sub">Already installed: {result.already.map((i) => workspaceLabel(i.workspace)).join(", ")}</div>}
        {result.pending.length > 0 && <div>Waiting for a first session: {result.pending.map(workspaceLabel).join(", ")}. The kit installs when a session starts there.</div>}
        {result.failed.map((f) => <div key={f.project} className="rd-kits-error">{workspaceLabel(f.workspace)} · {f.project}: {f.error}</div>)}
        {result.installed.length + result.already.length + result.pending.length + result.failed.length === 0 && <div className="rd-kits-sub">No workspace matches yet. Every workspace you create from now on gets the kit at its first session.</div>}
        {(kit.flows ?? []).length > 0 && <div className="rd-kits-note">Flows are enabled with the kit: {(kit.flows ?? []).map((f) => `/${f}`).join(", ")} in a session of an installed repository.</div>}
      </div>
      <div className="rd-kits-footer">
        <span />
        <div className="rd-kits-actions">
          <Button variant="ghost" onClick={onOpen}>Open {kit.name}</Button>
          <Button onClick={onDone}>Done</Button>
        </div>
      </div>
    </>
  );
}


function DetailRow({ kind, d }: { kind: string; d: KitDetail }) {
  const meta =
    kind === "skill" ? [d.effects, d.invocation === "user" ? "user-invoked" : ""].filter(Boolean).join(" · ")
    : kind === "agent" ? [d.model, (d.tools ?? []).slice(0, 5).join(", ")].filter(Boolean).join(" · ")
    : kind === "knowledge" ? d.load ?? ""
    : kind === "hook" ? `${d.event ?? ""}${d.match ? ` · ${d.match}` : ""}${d.blocking ? " · blocks" : ""}`
    : kind === "permission" ? `${d.rules?.length ?? 0} rules`
    : kind === "settings" ? `${d.harness ?? ""} · ${(d.keys ?? []).join(", ")}`
    : kind === "tool" ? d.form ?? ""
    : "";
  const title = kind === "skill" ? `/${d.name}` : d.name;
  return (
    <details className="rd-kits-skill">
      <summary><span className="rd-kits-mono">{title}</span>{meta && <span style={{ color: "var(--text-soft)" }}>{meta}</span>}</summary>
      <div className="rd-kits-note">
        {kind === "permission" && d.rules ? (
          <>
            {d.rules.filter((r) => r.decision === "deny").length > 0 && <div>never: {d.rules.filter((r) => r.decision === "deny").map((r) => `${r.tool} ${r.match}`).join(" · ")}</div>}
            {d.rules.filter((r) => r.decision === "ask").length > 0 && <div>asks: {d.rules.filter((r) => r.decision === "ask").map((r) => `${r.tool} ${r.match}`).join(" · ")}</div>}
            {d.rules.filter((r) => r.decision === "allow").length > 0 && <div>without asking: {d.rules.filter((r) => r.decision === "allow").map((r) => `${r.tool} ${r.match}`).join(" · ")}</div>}
          </>
        ) : (
          <>{d.description || "No description."}{kind === "knowledge" && d.lines ? ` (${d.lines} lines)` : ""}{kind === "skill" && (d.outputs ?? []).length > 0 ? ` · outputs: ${d.outputs!.join(", ")}` : ""}</>
        )}
      </div>
    </details>
  );
}
