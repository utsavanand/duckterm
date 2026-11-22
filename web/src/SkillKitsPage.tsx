import { useCallback, useEffect, useState } from "react";
import { api, KitCatalog, KitDetail, KitEntry, KitPlan, KitProcedure, KitWorkspace } from "./api";
import { DirBrowser } from "./DirBrowser";
import { Button, useToast } from "./ui";
import "./skillKits.css";

// The Skill kits page (Settings › Skill kits): the kits added to DuckTerm,
// what each holds, and the workspaces each is installed in. Add reads a
// kit's manifest and remembers it; Install into picks workspaces, shows the
// dry run for each of their repositories, then writes. A kit installs for
// every harness; the review names what a harness does not support.

type View =
  | { name: "list" }
  | { name: "add" }
  | { name: "kit"; kit: string }
  | { name: "pick"; kit: string }
  | { name: "review"; kit: string; options: Record<string, string>; plans: { workspace: string; project: string; plan: KitPlan }[] };

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
      setView({ name: "kit", kit: out.name });
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

  async function review(kit: KitEntry, targets: { workspace: string; project: string }[], options: Record<string, string>) {
    setBusy(true);
    try {
      const plans = [];
      for (const t of targets) {
        const out = await api.kitsPlan({ kit: kit.path, project: t.project, harnesses: catalog?.harnesses, options }, sessionKey);
        plans.push({ ...t, plan: out.plan });
      }
      setView({ name: "review", kit: kit.name, options, plans });
    } catch (e) {
      toast(`Review failed: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  async function install(kit: KitEntry, plans: { workspace: string; project: string }[], options: Record<string, string>, replace: boolean) {
    setBusy(true);
    let done = 0;
    try {
      for (const t of plans) {
        await api.kitsInstall({ kit: kit.path, project: t.project, harnesses: catalog?.harnesses, options, replace }, sessionKey);
        done += 1;
      }
      toast(`Installed ${kit.name} into ${done} repositor${done === 1 ? "y" : "ies"}`, "ok");
    } catch (e) {
      toast(`Install refused after ${done} of ${plans.length}: ${(e as Error).message}`, "err");
    } finally {
      await refresh();
      setView({ name: "kit", kit: kit.name });
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

  async function createTeam(project: string, kit: string, team: string, name: string) {
    setBusy(true);
    try {
      const out = await api.kitsCreateTeam({ project, kit, team, name }, sessionKey);
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
      {(view.name === "pick" || view.name === "review") && <><span>›</span><button onClick={() => setView({ name: "kit", kit: view.kit })}>{view.kit}</button><span>›</span><span>Install into</span></>}
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
            onInstall={() => setView({ name: "pick", kit: current.name })}
            onRemove={() => remove(current.name)}
            onVerify={verify}
            onUninstall={(project) => uninstall(current.name, project)}
            onCreateTeam={createTeam}
          />
        )}
        {view.name === "kit" && !current && <div className="rd-kits-error">{view.kit} is no longer added.</div>}
        {view.name === "pick" && current && (
          <PickView
            kit={current}
            workspaces={catalog?.workspaces ?? []}
            busy={busy}
            onBack={() => setView({ name: "kit", kit: current.name })}
            onReview={(targets, options) => review(current, targets, options)}
          />
        )}
        {view.name === "review" && current && (
          <ReviewView
            kit={current}
            plans={view.plans}
            busy={busy}
            onBack={() => setView({ name: "pick", kit: current.name })}
            onInstall={(replace) => install(current, view.plans, view.options, replace)}
          />
        )}
      </div>
    </div>
  );
}

function kitState(kit: KitEntry): { text: string; tone: string } {
  if (kit.error) return { text: "cannot be read", tone: "bad" };
  const errors = kit.inventory?.findings.filter((f) => f.level === "error").length ?? 0;
  if (errors) return { text: `${errors} manifest error${errors === 1 ? "" : "s"}`, tone: "bad" };
  if (kit.installed_in.length === 0) return { text: "not installed", tone: "mute" };
  const drifted = kit.installed_in.filter((i) => i.verify.ok === false).length;
  if (drifted) return { text: `drift in ${drifted} of ${kit.installed_in.length}`, tone: "bad" };
  const unchecked = kit.installed_in.filter((i) => i.verify.ok === null).length;
  if (unchecked) return { text: "installed, not yet verified", tone: "warn" };
  return { text: "verified", tone: "ok" };
}

function ListView({ catalog, error, busy, onAdd, onOpen, onClose, onCreateTeam }: {
  catalog: KitCatalog | null; error: string | null; busy: boolean;
  onAdd: () => void; onOpen: (name: string) => void; onClose: () => void;
  onCreateTeam: (project: string, kit: string, team: string, name: string) => void;
}) {
  const kits = catalog?.kits ?? [];
  const teams = kits.flatMap((k) => (k.procedures ?? []).map((p) => ({ kit: k, procedure: p })));
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>Skill kits</h1>
          <p>Kits added to DuckTerm and the workspaces they are installed in. A kit teaches every harness how a team works: knowledge, skills, agents, hooks, permissions, tools, flows, and sometimes a procedure.</p>
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
          <div className="rd-kits-row"><span>Kit</span><span>Installed in</span><span>State</span><span /></div>
          {kits.map((k) => {
            const state = kitState(k);
            const workspaces = Array.from(new Set(k.installed_in.map((i) => workspaceLabel(i.workspace))));
            return (
              <div className="rd-kits-row" key={k.name}>
                <div>
                  <div className="rd-kits-name">{k.name} <span className="rd-kits-mono" style={{ fontWeight: 400, color: "var(--text-soft)" }}>{k.version ?? ""}</span></div>
                  <div className="rd-kits-sub">{k.error ?? k.description ?? ""}</div>
                </div>
                <div className="rd-kits-pills">{workspaces.length ? workspaces.map((w) => <Pill key={w}>{w}</Pill>) : <span className="rd-kits-sub">nowhere yet</span>}</div>
                <div className="rd-kits-pills"><Pill tone={state.tone}>{state.text}</Pill>{(k.flows ?? []).length > 0 && <Pill>{k.flows!.length} flow{k.flows!.length === 1 ? "" : "s"}</Pill>}</div>
                <Button variant="ghost" size="sm" onClick={() => onOpen(k.name)}>Open</Button>
              </div>
            );
          })}
          {catalog && kits.length === 0 && <div className="rd-kits-row" style={{ gridTemplateColumns: "1fr" }}><span className="rd-kits-sub">No kit is added yet. Add one from a folder on this machine; nothing is installed until you choose a workspace.</span></div>}
        </div>
      </section>
      <section className="rd-kits-section">
        <h2>Teams these kits offer <span>a team is a workspace with a procedure; creating one makes a new workspace</span></h2>
        <div className="rd-kits-table">
          <div className="rd-kits-prow"><span>Team</span><span>Roles and rules</span><span /></div>
          {teams.map(({ kit, procedure }) => (
            <ProcedureRow key={`${kit.name}/${procedure.name}`} procedure={procedure} kit={kit} busy={busy} onCreate={(name) => onCreateTeam(kit.installed_in[0]?.project ?? "", kit.name, procedure.name, name)} />
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
      <CreateTeam name={procedure.name} busy={busy} installed={kit.installed_in.length > 0} onCreate={onCreate} />
    </div>
  );
}

function CreateTeam({ name, busy, installed, onCreate }: { name: string; busy: boolean; installed: boolean; onCreate: (name: string) => void }) {
  const [asking, setAsking] = useState(false);
  const [value, setValue] = useState(name);
  if (!asking) {
    return (
      <div className="rd-kits-pills" style={{ justifyContent: "flex-end" }} title={installed ? undefined : "Install the kit into a workspace first"}>
        <Button size="sm" onClick={() => setAsking(true)} disabled={busy || !installed}>Create team</Button>
      </div>
    );
  }
  return (
    <form className="rd-kits-actions" style={{ justifyContent: "flex-end", alignItems: "center" }} onSubmit={(e) => { e.preventDefault(); if (value.trim()) onCreate(value.trim()); setAsking(false); }}>
      <input type="text" aria-label="Workspace name" value={value} onChange={(e) => setValue(e.target.value)} style={{ width: 200 }} autoFocus />
      <Button size="sm" disabled={busy || !value.trim()} onClick={() => { if (value.trim()) { onCreate(value.trim()); setAsking(false); } }}>Create workspace</Button>
      <Button size="sm" variant="ghost" onClick={() => setAsking(false)}>Cancel</Button>
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
        <span className="rd-kits-sub">reads the manifest; nothing is installed yet</span>
      </div>
      <form className="rd-kits-form" style={{ gridTemplateColumns: "1fr" }} onSubmit={(e) => { e.preventDefault(); if (path.trim()) onAdd(path.trim()); }}>
        <div>
          <label htmlFor="rd-kits-kit" style={LABEL}>Kit folder</label>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input id="rd-kits-kit" type="text" value={path} onChange={(e) => setPath(e.target.value)} placeholder="~/Downloads/uv-suite" autoFocus style={{ flex: 1 }} />
            <Button variant="ghost" onClick={() => setBrowsing((b) => !b)}>Browse…</Button>
          </div>
          <div className="rd-kits-note">The folder a kit was downloaded or cloned into; it holds skill-kit.toml. When one folder holds several kits, type the path of the .toml you want. Only the manifest is read; no kit code runs.</div>
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
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button onClick={() => onAdd(path.trim())} disabled={busy || !path.trim()}>Add</Button>
        </div>
      </div>
    </>
  );
}

function KitView({ kit, busy, onInstall, onRemove, onVerify, onUninstall, onCreateTeam }: {
  kit: KitEntry; busy: boolean; onInstall: () => void; onRemove: () => void;
  onVerify: (project: string) => void; onUninstall: (project: string) => void;
  onCreateTeam: (project: string, kit: string, team: string, name: string) => void;
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
  const firstProject = kit.installed_in[0]?.project ?? "";
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>{kit.name} <span className="rd-kits-mono" style={{ fontWeight: 400, color: "var(--text-soft)" }}>{kit.version ?? ""}</span></h1>
          <p>{kit.error ?? kit.description} · <span className="rd-kits-mono">{kit.path}</span></p>
        </div>
        <div className="rd-kits-actions">
          <Button onClick={onInstall} disabled={busy || !!kit.error || errors.length > 0}>Install into…</Button>
          {!confirmRemove ? <Button variant="ghost" onClick={() => setConfirmRemove(true)}>Remove from DuckTerm…</Button> : <Button variant="danger" onClick={onRemove} disabled={busy}>Confirm remove</Button>}
        </div>
      </div>
      {confirmRemove && <div className="rd-kits-note">Forgets the kit here. Repositories it is installed in keep their files; uninstall those first if you want them gone.</div>}
      {kit.error && <div className="rd-kits-error">{kit.error}</div>}
      {kit.inventory && (
        <div className="rd-kits-pills">
          <Pill tone={errors.length ? "bad" : "ok"}>{errors.length ? `${errors.length} manifest errors` : "validate ok"}{warnings ? ` · ${warnings} warnings` : ""}</Pill>
          <Pill>{kit.inventory.component_count} components</Pill>
          {Object.keys(kit.inventory.options).length > 0 && <Pill>options: {Object.keys(kit.inventory.options).join(", ")}</Pill>}
        </div>
      )}
      {errors.map((f) => <div key={f.component + f.message} className="rd-kits-error">{f.component}: {f.message}</div>)}
      <section className="rd-kits-section">
        <h2>Installed in <span>{kit.installed_in.length ? `${kit.installed_in.length} repositor${kit.installed_in.length === 1 ? "y" : "ies"}` : "no workspace yet"}</span></h2>
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
          {kit.installed_in.length === 0 && <div className="rd-kits-row" style={{ gridTemplateColumns: "1fr" }}><span className="rd-kits-sub">Not installed in any workspace yet. Install into… lists your workspaces.</span></div>}
        </div>
        {confirmUninstall && <div className="rd-kits-note">Removes exactly what the install record lists and restores every edited file byte for byte when you have not touched it since.</div>}
      </section>
      {(kit.procedures ?? []).map((p) => (
        <div className="rd-kits-card" key={p.name} style={{ borderColor: "var(--brand-green)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}><strong>Team: {p.name}</strong><CreateTeam name={p.name} busy={busy} installed={!!firstProject} onCreate={(name) => onCreateTeam(firstProject, kit.name, p.name, name)} /></div>
          <div style={{ fontSize: "0.75rem" }}>{Object.keys(p.roles).join(" · ")} · {p.rules.map(describeRule).join(" · ")}</div>
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
    </>
  );
}

function PickView({ kit, workspaces, busy, onBack, onReview }: {
  kit: KitEntry; workspaces: KitWorkspace[]; busy: boolean; onBack: () => void;
  onReview: (targets: { workspace: string; project: string }[], options: Record<string, string>) => void;
}) {
  const installed = new Set(kit.installed_in.map((i) => i.project));
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const optionSpecs = kit.inventory?.options ?? {};
  const [options, setOptions] = useState<Record<string, string>>(Object.fromEntries(Object.entries(optionSpecs).flatMap(([n, s]) => (s.default ? [[n, s.default]] : []))));
  const toggle = (project: string) => setChosen((s) => { const next = new Set(s); if (next.has(project)) next.delete(project); else next.add(project); return next; });
  const targets = workspaces.flatMap((w) => w.repositories.filter((r) => chosen.has(r)).map((r) => ({ workspace: w.name, project: r })));
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>Install {kit.name} into…</h1>
          <p>Step 1 of 2 · a kit installs into the repository a workspace's sessions run in, and is committed there. Every harness gets it; the review says what each one takes.</p>
        </div>
      </div>
      <div className="rd-kits-table">
        <div className="rd-kits-row" style={{ gridTemplateColumns: "1fr" }}><span>Workspaces</span></div>
        {workspaces.map((w) => (
          <div className="rd-kits-row" style={{ gridTemplateColumns: "1fr" }} key={w.name || "(ungrouped)"}>
            <div>
              <div className="rd-kits-name">{workspaceLabel(w.name)}</div>
              {w.repositories.length === 0 && <div className="rd-kits-sub">no session has run here yet, so there is no repository to install into</div>}
              {w.repositories.map((r) => (
                <label className="rd-kits-check" key={r} style={{ marginTop: 6 }}>
                  <input type="checkbox" checked={chosen.has(r)} disabled={installed.has(r)} onChange={() => toggle(r)} aria-label={`${workspaceLabel(w.name)} ${r}`} />
                  <span className="rd-kits-mono">{r}</span>
                  {installed.has(r) && <Pill tone="ok">installed</Pill>}
                </label>
              ))}
            </div>
          </div>
        ))}
        {workspaces.length === 0 && <div className="rd-kits-row" style={{ gridTemplateColumns: "1fr" }}><span className="rd-kits-sub">No workspace yet. New › New workspace, then start a session in it.</span></div>}
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
      <div className="rd-kits-footer">
        <span>{targets.length} repositor{targets.length === 1 ? "y" : "ies"} chosen</span>
        <div className="rd-kits-actions">
          <Button variant="ghost" onClick={onBack}>Back</Button>
          <Button onClick={() => onReview(targets, options)} disabled={busy || targets.length === 0}>Review what will change</Button>
        </div>
      </div>
    </>
  );
}

function ReviewView({ kit, plans, busy, onBack, onInstall }: {
  kit: KitEntry; plans: { workspace: string; project: string; plan: KitPlan }[]; busy: boolean; onBack: () => void; onInstall: (replace: boolean) => void;
}) {
  const [replace, setReplace] = useState(false);
  const soft = plans.flatMap((p) => p.plan.conflicts.filter((c) => c.kind === "file-exists").map((c) => ({ ...c, project: p.project })));
  const hard = plans.flatMap((p) => p.plan.conflicts.filter((c) => c.kind !== "file-exists").map((c) => ({ ...c, project: p.project })));
  const errors = plans.flatMap((p) => p.plan.findings.filter((f) => f.level === "error"));
  const hooks = kit.inventory?.components.hook?.length ?? 0;
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>Review: {kit.name} {kit.version ?? ""} → {plans.length} repositor{plans.length === 1 ? "y" : "ies"}</h1>
          <p>Step 2 of 2 · a dry run; nothing is written until you press Install</p>
        </div>
        <div className="rd-kits-pills">
          <Pill tone={errors.length ? "bad" : "ok"}>{errors.length ? `${errors.length} errors` : "validate ok"}</Pill>
          <Pill tone={hard.length ? "bad" : soft.length ? "warn" : "ok"}>{soft.length + hard.length} conflict{soft.length + hard.length === 1 ? "" : "s"}</Pill>
        </div>
      </div>
      {plans.map(({ workspace, project, plan }) => <PlanBlock key={project} workspace={workspace} project={project} plan={plan} />)}
      <div className="rd-kits-card" style={hard.length || soft.length ? { borderColor: hard.length ? "#b4262a" : "#b8860b" } : undefined}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><strong>Conflicts</strong><Pill tone={hard.length ? "bad" : soft.length ? "warn" : "ok"}>{soft.length} soft · {hard.length} hard</Pill></div>
        {soft.length + hard.length === 0 && <div className="rd-kits-sub">Nothing in these repositories is at a path this kit writes.</div>}
        {soft.map((c) => <div key={c.project + c.path} style={{ fontSize: "0.75rem" }}><span className="rd-kits-mono">{c.path}</span> {c.detail} <span className="rd-kits-sub">({c.project})</span></div>)}
        {soft.length > 0 && (
          <label className="rd-kits-check"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> replace these files with the kit's (otherwise they are kept and the component skipped)</label>
        )}
        {hard.map((c) => <div key={c.project + c.path + c.kind} style={{ fontSize: "0.75rem", color: "#a52a2a" }}><span className="rd-kits-mono">{c.path || c.component}</span> {c.kind}: {c.detail} <span className="rd-kits-sub">({c.project})</span></div>)}
        {hard.length > 0 && <div className="rd-kits-note">A hard conflict refuses the install; there is no override.</div>}
        {errors.map((f) => <div key={f.component + f.message} style={{ fontSize: "0.75rem", color: "#a52a2a" }}>{f.component}: {f.message}</div>)}
      </div>
      <div className="rd-kits-footer">
        <span>No kit script runs during review or install. {hooks} hook script{hooks === 1 ? "" : "s"} run later, inside the harness, through DuckTerm's shim.</span>
        <div className="rd-kits-actions">
          <Button variant="ghost" onClick={onBack}>Back</Button>
          <Button onClick={() => onInstall(replace)} disabled={busy || hard.length > 0 || errors.length > 0}>Install into {plans.length} repositor{plans.length === 1 ? "y" : "ies"}</Button>
        </div>
      </div>
    </>
  );
}

function PlanBlock({ workspace, project, plan }: { workspace: string; project: string; plan: KitPlan }) {
  const noteFor = (row: { notes: string[] }, h: string) =>
    row.notes.filter((n) => !/^[a-z-]+: /.test(n) || n.startsWith(`${h}: `)).map((n) => n.replace(`${h}: `, "")).join(" · ");
  const unsupported = plan.harnesses.map((h) => ({ harness: h, count: plan.rows.filter((r) => r.harnesses[h] === "not supported").length })).filter((x) => x.count > 0);
  const installed = plan.rows.filter((r) => Object.values(r.harnesses).some((s) => s === "installed")).length;
  return (
    <div className="rd-kits-card">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <div><strong>{workspaceLabel(workspace)}</strong> <span className="rd-kits-mono" style={{ color: "var(--text-soft)" }}>{project}</span></div>
        <div className="rd-kits-pills">
          <Pill>{installed} of {plan.rows.length} components · {plan.files.length} files</Pill>
          {unsupported.map((u) => <Pill key={u.harness} tone="warn">{u.harness}: {u.count} not supported</Pill>)}
        </div>
      </div>
      <div className="rd-kits-plan">
        <table>
          <thead><tr><th style={{ width: "30%" }}>Component</th>{plan.harnesses.map((h) => <th key={h}>{h}</th>)}</tr></thead>
          <tbody>
            {plan.rows.map((row) => {
              const [, kind, name] = row.component.split("/");
              return (
                <tr key={row.component} style={kind === "team" ? { background: "var(--bg-soft)" } : undefined}>
                  <td><div>{kind === "team" ? "procedure" : kind} · <span className="rd-kits-mono">{name}</span></div></td>
                  {plan.harnesses.map((h) => (
                    <td key={h}><StatusPill status={row.harnesses[h] ?? "—"} /><div className="rd-kits-note">{noteFor(row, h)}</div></td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <details>
        <summary className="rd-kits-sub">Files ({plan.files.length})</summary>
        <pre className="rd-kits-files">{plan.files.map((f) => `${f.op.padEnd(13)}${f.path}${f.harness !== "shared" ? ` (${f.harness})` : ""}`).join("\n")}</pre>
        <div className="rd-kits-note">Edited files are snapshotted first; uninstall restores them byte for byte when untouched.</div>
      </details>
    </div>
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
