import { useCallback, useEffect, useMemo, useState } from "react";
import { api, KitInstalled, KitInventory, KitPlan, KitProcedure, KitRunSummary, KitsOverview } from "./api";
import { Button, useToast } from "./ui";
import "./skillKits.css";

// The Skill kits page (Settings › Skill kits): the kits installed in a
// workspace's repository, the procedures they ship, and the add flow —
// pick a kit folder, review the plan, install. Every number on it comes from
// the server's kit engine, the same one `duckterm kit` uses.

type View =
  | { name: "list" }
  | { name: "add" }
  | { name: "review"; inventory: KitInventory; plan: KitPlan; request: { kit: string; harnesses: string[]; options: Record<string, string> } }
  | { name: "kit"; kit: KitInstalled };

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

function Pill({ children, tone }: { children: React.ReactNode; tone?: string }) {
  return <span className={`rd-kit-pill${tone ? ` ${tone}` : ""}`}>{children}</span>;
}

function StatusPill({ status }: { status: string }) {
  return <Pill tone={STATUS_CLASS[status] ?? ""}>{status}</Pill>;
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
  defaultDir,
  knownDirs,
  sessionKey = "",
  onClose,
  fallback,
}: {
  defaultDir: string | null;
  knownDirs: string[];
  sessionKey?: string;
  onClose: () => void;
  fallback: React.ReactNode;
}) {
  const toast = useToast();
  const [project, setProject] = useState(defaultDir ?? knownDirs[0] ?? "");
  const [overview, setOverview] = useState<KitsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [view, setView] = useState<View>({ name: "list" });
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!project.trim()) { setOverview(null); return; }
    try {
      setOverview(await api.kitsOverview(project.trim(), sessionKey));
      setError(null);
    } catch (e) {
      const message = (e as Error).message;
      if (/still in development|unavailable in Duckterm/i.test(message)) { setUnavailable(true); return; }
      setError(message);
    }
  }, [project, sessionKey]);

  useEffect(() => { refresh(); }, [refresh]);

  if (unavailable) return <>{fallback}</>;

  async function plan(request: { kit: string; harnesses: string[]; options: Record<string, string> }) {
    setBusy(true);
    try {
      const out = await api.kitsPlan({ kit: request.kit, project: project.trim(), harnesses: request.harnesses, options: request.options }, sessionKey);
      setView({ name: "review", inventory: out.inventory, plan: out.plan, request });
    } catch (e) {
      toast(`Review failed: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  async function install(request: { kit: string; harnesses: string[]; options: Record<string, string> }, replace: boolean) {
    setBusy(true);
    try {
      const out = await api.kitsInstall({ kit: request.kit, project: project.trim(), harnesses: request.harnesses, options: request.options, replace }, sessionKey);
      toast(`Installed ${out.report.kit}: ${out.report.written.length} entries`, "ok");
      const next = await api.kitsOverview(project.trim(), sessionKey);
      setOverview(next);
      const kit = next.kits.find((k) => k.name === out.report.kit);
      setView(kit ? { name: "kit", kit } : { name: "list" });
    } catch (e) {
      toast(`Install refused: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  async function uninstall(name: string) {
    setBusy(true);
    try {
      const out = await api.kitsUninstall(project.trim(), name, sessionKey);
      toast(`Uninstalled ${name}: ${out.report.removed.length} removed, ${out.report.kept.length} kept`, "ok");
      await refresh();
      setView({ name: "list" });
    } catch (e) {
      toast(`Uninstall refused: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  async function verifyAll() {
    try {
      const out = await api.kitsVerify(project.trim(), sessionKey);
      const drift = out.reports.filter((r) => r.missing.length || r.drifted.length);
      toast(drift.length ? `${drift.length} kit(s) drifted` : "every kit verifies", drift.length ? "err" : "ok");
      await refresh();
    } catch (e) {
      toast(`Verify failed: ${(e as Error).message}`, "err");
    }
  }

  const crumb = (
    <div className="rd-kits-crumb">
      <button onClick={onClose}>Settings</button><span>›</span>
      {view.name === "list" ? <span>Skill kits</span> : <button onClick={() => setView({ name: "list" })}>Skill kits</button>}
      {view.name === "add" && <><span>›</span><span>Add</span></>}
      {view.name === "review" && <><span>›</span><button onClick={() => setView({ name: "add" })}>Add</button><span>›</span><span>Review</span></>}
      {view.name === "kit" && <><span>›</span><span>{view.kit.name}</span></>}
    </div>
  );

  return (
    <div className="rd-kits-page" role="dialog" aria-label="Skill kits">
      <div className="rd-kits-main">
        {crumb}
        {view.name === "list" && (
          <ListView
            project={project}
            knownDirs={knownDirs}
            onProject={setProject}
            overview={overview}
            error={error}
            onAdd={() => setView({ name: "add" })}
            onOpen={(kit) => setView({ name: "kit", kit })}
            onVerifyAll={verifyAll}
            onClose={onClose}
          />
        )}
        {view.name === "add" && (
          <AddView
            project={project}
            knownDirs={knownDirs}
            onProject={setProject}
            harnessesPresent={overview?.harnesses_present ?? {}}
            busy={busy}
            onCancel={() => setView({ name: "list" })}
            onReview={plan}
            sessionKey={sessionKey}
          />
        )}
        {view.name === "review" && (
          <ReviewView
            project={project}
            inventory={view.inventory}
            plan={view.plan}
            busy={busy}
            onBack={() => setView({ name: "add" })}
            onInstall={(replace) => install(view.request, replace)}
          />
        )}
        {view.name === "kit" && (
          <KitView
            kit={view.kit}
            runs={(overview?.runs ?? []).filter((r) => r.flow.startsWith(view.kit.name + "/"))}
            busy={busy}
            onVerify={verifyAll}
            onUninstall={() => uninstall(view.kit.name)}
          />
        )}
      </div>
    </div>
  );
}

function ProjectPicker({ project, knownDirs, onProject, label }: { project: string; knownDirs: string[]; onProject: (v: string) => void; label: string }) {
  const id = "rd-kits-project";
  return (
    <div>
      <label htmlFor={id} style={{ display: "block", fontSize: "0.6875rem", letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-soft)", marginBottom: 6 }}>{label}</label>
      <input id={id} type="text" list="rd-kits-dirs" value={project} onChange={(e) => onProject(e.target.value)} placeholder="~/ws/my-project" />
      <datalist id="rd-kits-dirs">{knownDirs.map((d) => <option key={d} value={d} />)}</datalist>
      <div className="rd-kits-note">A kit installs into the repository a workspace works in and is committed there, so every workspace on that repository shares it.</div>
    </div>
  );
}

function ListView({ project, knownDirs, onProject, overview, error, onAdd, onOpen, onVerifyAll, onClose }: {
  project: string; knownDirs: string[]; onProject: (v: string) => void;
  overview: KitsOverview | null; error: string | null;
  onAdd: () => void; onOpen: (kit: KitInstalled) => void; onVerifyAll: () => void; onClose: () => void;
}) {
  const kits = overview?.kits ?? [];
  const procedures = overview?.procedures ?? [];
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>Skill kits</h1>
          <p>Kits installed in the repository this workspace works in. A kit teaches every harness how a team works: knowledge, skills, agents, hooks, permissions, tools, flows, and sometimes a procedure.</p>
        </div>
        <div className="rd-kits-actions">
          <Button variant="ghost" onClick={onVerifyAll} disabled={!kits.length}>Verify all</Button>
          <Button onClick={onAdd}>Add skill kit…</Button>
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </div>
      </div>
      <ProjectPicker project={project} knownDirs={knownDirs} onProject={onProject} label="Workspace repository" />
      {error && <div className="rd-kits-error">{error}</div>}
      <section className="rd-kits-section">
        <h2>Installed kits <span>{overview ? `${kits.length} in ${overview.project}` : ""}</span></h2>
        <div className="rd-kits-table">
          <div className="rd-kits-row"><span>Kit</span><span>Installed for</span><span>State</span><span /></div>
          {kits.map((k) => (
            <div className="rd-kits-row" key={k.name}>
              <div>
                <div className="rd-kits-name">{k.name} <span className="rd-kits-mono" style={{ fontWeight: 400, color: "var(--text-soft)" }}>{k.version ?? ""}</span></div>
                <div className="rd-kits-sub">{k.components.length} components{Object.keys(k.options).length ? ` · ${Object.entries(k.options).map(([o, c]) => `${o} ${c}`).join(", ")}` : ""}{k.procedures.length ? ` · ships ${k.procedures.map((p) => p.name).join(", ")}` : ""}</div>
              </div>
              <div className="rd-kits-pills">{k.harnesses.map((h) => <Pill key={h}>{h}</Pill>)}</div>
              <div className="rd-kits-pills">
                {k.verify.ok === true && <Pill tone="ok">verified · {k.entries} entries</Pill>}
                {k.verify.ok === false && <Pill tone="bad">drift: {k.verify.missing.length} missing, {k.verify.drifted.length} drifted</Pill>}
                {!k.trusted && <Pill tone="warn">record not written here</Pill>}
                {k.flows.length > 0 && <Pill>{k.flows.length} flow{k.flows.length === 1 ? "" : "s"}</Pill>}
              </div>
              <Button variant="ghost" size="sm" onClick={() => onOpen(k)}>Open</Button>
            </div>
          ))}
          {overview && kits.length === 0 && <div className="rd-kits-row" style={{ gridTemplateColumns: "1fr" }}><span className="rd-kits-sub">No kit is installed in this repository yet.</span></div>}
        </div>
      </section>
      <section className="rd-kits-section">
        <h2>Teams these kits offer <span>a team is a workspace with a procedure; creating one makes a new workspace</span></h2>
        <div className="rd-kits-table">
          <div className="rd-kits-prow"><span>Team</span><span>Roles and rules</span><span /></div>
          {procedures.map((p) => <ProcedureRow key={`${p.kit}/${p.name}`} procedure={p} />)}
          {overview && procedures.length === 0 && <div className="rd-kits-prow" style={{ gridTemplateColumns: "1fr" }}><span className="rd-kits-sub">None of the installed kits offers a team.</span></div>}
        </div>
      </section>
    </>
  );
}

function ProcedureRow({ procedure }: { procedure: KitProcedure }) {
  return (
    <div className="rd-kits-prow">
      <div>
        <div className="rd-kits-name">{procedure.name} <span className="rd-kits-mono" style={{ fontWeight: 400, color: "var(--text-soft)" }}>from {procedure.kit}</span></div>
        <div className="rd-kits-sub">{Object.keys(procedure.roles).join(" · ")}</div>
      </div>
      <div style={{ fontSize: "0.75rem" }}>{procedure.rules.map(describeRule).join(" · ") || procedure.description}</div>
      <div className="rd-kits-pills" style={{ justifyContent: "flex-end" }}><Button size="sm" disabled>Create team</Button></div>
    </div>
  );
}

function AddView({ project, knownDirs, onProject, harnessesPresent, busy, onCancel, onReview, sessionKey }: {
  project: string; knownDirs: string[]; onProject: (v: string) => void;
  harnessesPresent: Record<string, boolean>; busy: boolean;
  onCancel: () => void; onReview: (r: { kit: string; harnesses: string[]; options: Record<string, string> }) => void; sessionKey: string;
}) {
  const [kit, setKit] = useState("");
  const [inventory, setInventory] = useState<KitInventory | null>(null);
  const [inventoryError, setInventoryError] = useState<string | null>(null);
  const [harnesses, setHarnesses] = useState<string[]>(Object.entries(harnessesPresent).filter(([, present]) => present).map(([h]) => h));
  const [options, setOptions] = useState<Record<string, string>>({});
  const allHarnesses = useMemo(() => Object.keys(harnessesPresent).length ? Object.keys(harnessesPresent) : ["claude-code", "codex", "copilot", "opencode"], [harnessesPresent]);

  useEffect(() => {
    if (!kit.trim() || !project.trim()) { setInventory(null); return; }
    const handle = setTimeout(async () => {
      try {
        const out = await api.kitsPlan({ kit: kit.trim(), project: project.trim(), harnesses: harnesses.length ? harnesses : undefined }, sessionKey);
        setInventory(out.inventory);
        setInventoryError(null);
        setOptions((current) => {
          const next = { ...current };
          for (const [name, spec] of Object.entries(out.inventory.options)) if (!(name in next) && spec.default) next[name] = spec.default;
          return next;
        });
      } catch (e) {
        setInventory(null);
        setInventoryError((e as Error).message);
      }
    }, 400);
    return () => clearTimeout(handle);
  }, [kit, project, harnesses, sessionKey]);

  const counts = inventory ? Object.entries(inventory.components) : [];
  return (
    <>
      <div className="rd-kits-head">
        <h1>Add skill kit</h1>
        <span className="rd-kits-sub">Step 1 of 2 · nothing is written yet</span>
      </div>
      <div className="rd-kits-form">
        <div>
          <div>
            <label htmlFor="rd-kits-kit" style={{ display: "block", fontSize: "0.6875rem", letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-soft)", marginBottom: 6 }}>Kit folder (holds skill-kit.toml)</label>
            <input id="rd-kits-kit" type="text" value={kit} onChange={(e) => setKit(e.target.value)} placeholder="~/ws-uv-suite" />
            <div className="rd-kits-note">A folder on this machine. The manifest is read; no kit code runs.</div>
            {inventoryError && <div className="rd-kits-error" style={{ marginTop: 8 }}>{inventoryError}</div>}
          </div>
          <ProjectPicker project={project} knownDirs={knownDirs} onProject={onProject} label="Install into" />
          <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 8 }}>
            <legend style={{ fontSize: "0.6875rem", letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-soft)", marginBottom: 6 }}>Install for</legend>
            {allHarnesses.map((h) => (
              <label className="rd-kits-check" key={h}>
                <input type="checkbox" checked={harnesses.includes(h)} disabled={harnessesPresent[h] === false}
                  onChange={(e) => setHarnesses(e.target.checked ? [...harnesses, h] : harnesses.filter((x) => x !== h))} />
                {h} <Pill>{harnessesPresent[h] === false ? "not installed here" : "on PATH"}</Pill>
              </label>
            ))}
          </fieldset>
          {inventory && Object.entries(inventory.options).map(([name, spec]) => (
            <div key={name}>
              <label htmlFor={`rd-kits-opt-${name}`} style={{ display: "block", fontSize: "0.6875rem", letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-soft)", marginBottom: 6 }}>{name}</label>
              <select id={`rd-kits-opt-${name}`} value={options[name] ?? spec.default ?? ""} onChange={(e) => setOptions({ ...options, [name]: e.target.value })}>
                {(spec.choices ?? []).map((c) => <option key={c} value={c}>{c}{c === spec.default ? " (default)" : ""}</option>)}
              </select>
              <div className="rd-kits-note">{spec.description ?? "A kit option: each choice turns components on or off."}</div>
            </div>
          ))}
        </div>
        <aside className="rd-kits-aside">
          {inventory ? (
            <>
              <div className="rd-kits-aside-head">
                <div>
                  <div className="rd-kits-name">{inventory.name} <span className="rd-kits-mono" style={{ fontWeight: 400, color: "var(--text-soft)" }}>{inventory.version}{inventory.license ? ` · ${inventory.license}` : ""}</span></div>
                  <div className="rd-kits-sub">{inventory.description}</div>
                </div>
                <Pill tone={inventory.valid ? "ok" : "bad"}>{inventory.valid ? "validate: ok" : "validate: errors"}</Pill>
              </div>
              {counts.map(([kind, items]) => (
                <div className="rd-kits-kind" key={kind}><span>{kind} · {items.length}</span><span className="rd-kits-mono" style={{ textAlign: "right" }}>{items.map((i) => i.name).join(" · ")}</span></div>
              ))}
              {inventory.procedures.map((p) => (
                <div className="rd-kits-kind procedure" key={p.name}><span>procedure</span><span className="rd-kits-mono">{p.name} · {Object.keys(p.roles).join(", ")} · {p.rules.length} rules</span></div>
              ))}
              <div className="rd-kits-note" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
                {inventory.component_count} components · {inventory.findings.filter((f) => f.level === "warning").length} warnings · {inventory.findings.filter((f) => f.level === "error").length} errors. A procedure is offered after install; applying it to a workspace is your choice.
              </div>
            </>
          ) : (
            <div className="rd-kits-aside-head"><span className="rd-kits-sub">Point at a kit folder to see what it holds.</span></div>
          )}
        </aside>
      </div>
      <div className="rd-kits-footer">
        <span />
        <div className="rd-kits-actions">
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button onClick={() => onReview({ kit: kit.trim(), harnesses, options })} disabled={busy || !inventory?.valid || harnesses.length === 0}>Review what will change</Button>
        </div>
      </div>
    </>
  );
}

function ReviewView({ project, inventory, plan, busy, onBack, onInstall }: {
  project: string; inventory: KitInventory; plan: KitPlan; busy: boolean; onBack: () => void; onInstall: (replace: boolean) => void;
}) {
  const soft = plan.conflicts.filter((c) => c.kind === "file-exists");
  const hard = plan.conflicts.filter((c) => c.kind !== "file-exists");
  const [replace, setReplace] = useState(false);
  const errors = plan.findings.filter((f) => f.level === "error");
  const installed = plan.rows.filter((r) => Object.values(r.harnesses).some((s) => s === "installed")).length;
  const noteFor = (row: { notes: string[] }, h: string) =>
    row.notes.filter((n) => !/^[a-z-]+: /.test(n) || n.startsWith(`${h}: `)).map((n) => n.replace(`${h}: `, "")).join(" · ");
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>Review: {plan.kit.name} {plan.kit.version} → {project}</h1>
          <p>Step 2 of 2 · for {plan.harnesses.join(" and ")}{Object.keys(plan.options).length ? ` · ${Object.entries(plan.options).map(([o, c]) => `${o}=${c}`).join(", ")}` : ""} · a dry run; nothing is written until you press Install</p>
        </div>
        <div className="rd-kits-pills">
          <Pill tone={errors.length ? "bad" : "ok"}>{errors.length ? `${errors.length} errors` : "validate ok"}{plan.findings.length - errors.length ? ` · ${plan.findings.length - errors.length} warnings` : ""}</Pill>
          <Pill tone={hard.length ? "bad" : soft.length ? "warn" : "ok"}>{plan.conflicts.length} conflict{plan.conflicts.length === 1 ? "" : "s"}</Pill>
          <Pill>{plan.rows.length} components · {plan.files.length} files</Pill>
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
      <div className="rd-kits-cards">
        <div className="rd-kits-card" style={hard.length || soft.length ? { borderColor: hard.length ? "#b4262a" : "#b8860b" } : undefined}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><strong>Conflicts</strong><Pill tone={hard.length ? "bad" : soft.length ? "warn" : "ok"}>{soft.length} soft · {hard.length} hard</Pill></div>
          {plan.conflicts.length === 0 && <div className="rd-kits-sub">Nothing in the repository is at a path this kit writes.</div>}
          {soft.map((c) => <div key={c.path} style={{ fontSize: "0.75rem" }}><span className="rd-kits-mono">{c.path}</span> {c.detail}</div>)}
          {soft.length > 0 && (
            <label className="rd-kits-check"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> replace these files with the kit's (otherwise they are kept and the component skipped)</label>
          )}
          {hard.map((c) => <div key={c.path + c.kind} style={{ fontSize: "0.75rem", color: "#a52a2a" }}><span className="rd-kits-mono">{c.path || c.component}</span> {c.kind}: {c.detail}</div>)}
          {hard.length > 0 && <div className="rd-kits-note">A hard conflict refuses the install; there is no override.</div>}
          {errors.map((f) => <div key={f.component + f.message} style={{ fontSize: "0.75rem", color: "#a52a2a" }}>{f.component}: {f.message}</div>)}
        </div>
        <div className="rd-kits-card">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><strong>Files</strong><span className="rd-kits-mono" style={{ color: "var(--text-soft)" }}>{Object.entries(plan.files.reduce<Record<string, number>>((acc, f) => ({ ...acc, [f.op]: (acc[f.op] ?? 0) + 1 }), {})).map(([op, n]) => `${n} ${op}`).join(" · ")}</span></div>
          <pre className="rd-kits-files">{plan.files.map((f) => `${f.op.padEnd(13)}${f.path}${f.harness !== "shared" ? ` (${f.harness})` : ""}`).join("\n")}</pre>
          <div className="rd-kits-note">Edited files are snapshotted first; uninstall restores them byte for byte when untouched.</div>
        </div>
      </div>
      <div className="rd-kits-footer">
        <span>No kit script runs during review or install. {inventory.components.hook?.length ?? 0} hook script{(inventory.components.hook?.length ?? 0) === 1 ? "" : "s"} run later, inside the harness, through DuckTerm's shim.</span>
        <div className="rd-kits-actions">
          <Button variant="ghost" onClick={onBack}>Back</Button>
          <Button onClick={() => onInstall(replace)} disabled={busy || hard.length > 0 || errors.length > 0}>Install {installed} components</Button>
        </div>
      </div>
    </>
  );
}

function KitView({ kit, runs, busy, onVerify, onUninstall }: {
  kit: KitInstalled; runs: KitRunSummary[]; busy: boolean; onVerify: () => void; onUninstall: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const byKind = kit.components.reduce<Record<string, string[]>>((acc, c) => {
    const [, kind, name] = c.split("/");
    (acc[kind] ??= []).push(name);
    return acc;
  }, {});
  const counts = Object.entries(byKind).filter(([kind]) => kind !== "team").map(([kind, names]) => `${names.length} ${kind}${names.length === 1 ? "" : "s"}`).join(", ");
  const last = runs[runs.length - 1];
  return (
    <>
      <div className="rd-kits-head">
        <div>
          <h1>{kit.name} <span className="rd-kits-mono" style={{ fontWeight: 400, color: "var(--text-soft)" }}>{kit.version ?? ""}</span></h1>
          <p>Installed for {kit.harnesses.join(" and ")} · {kit.verify.ok === true ? <Pill tone="ok">verified</Pill> : kit.verify.ok === false ? <Pill tone="bad">drift: {kit.verify.missing.length} missing, {kit.verify.drifted.length} drifted</Pill> : <Pill>not verified</Pill>}{!kit.trusted && <> · <Pill tone="warn">record written on another machine</Pill></>}</p>
        </div>
        <div className="rd-kits-actions">
          <Button variant="ghost" onClick={onVerify}>Verify</Button>
          {!confirm ? <Button variant="danger" onClick={() => setConfirm(true)}>Uninstall…</Button> : <Button variant="danger" onClick={onUninstall} disabled={busy}>Confirm uninstall</Button>}
        </div>
      </div>
      {confirm && <div className="rd-kits-note">Uninstall removes exactly what the install record lists and restores every edited file byte for byte when you have not touched it since. Files you edited keep your changes; only the kit's additions come out.</div>}
      {kit.procedures.map((p) => (
        <div className="rd-kits-card" key={p.name} style={{ borderColor: "var(--brand-green)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}><strong>Team: {p.name}</strong><Button size="sm" disabled>Create team</Button></div>
          <div style={{ fontSize: "0.75rem" }}>{Object.keys(p.roles).join(" · ")}. {p.rules.map(describeRule).join("; ")}.</div>
          <div className="rd-kits-note">Creates a new workspace with this procedure and one card per role; existing agents can be moved into it afterwards. Not built yet.</div>
        </div>
      ))}
      <div className="rd-kits-cards">
        <div className="rd-kits-card">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><strong>Flows</strong><span className="rd-kits-sub">enabled on install</span></div>
          {kit.flows.length === 0 && <div className="rd-kits-sub">This kit ships no flow.</div>}
          {kit.flows.map((flow) => (
            <div className="rd-kits-line" key={flow}>
              <span className="rd-kits-mono">{flow}</span>
              <span className="rd-kits-mono" style={{ color: "var(--text-soft)" }}>no trigger · duckterm kit run {kit.name}/{flow}</span>
            </div>
          ))}
          {kit.flows.length > 0 && <div className="rd-kits-note">A flow with a trigger runs on its own; the others wait for their shortcut.</div>}
          {last && (
            <div className="rd-kits-line">
              <div className="rd-kits-note">Last run · {last.flow.split("/")[1]} · {last.state}{last.current ? ` at ${last.current.step}` : ""}{last.failure ? `: ${last.failure.reason}` : ""}</div>
              <Pill tone={last.state === "succeeded" ? "ok" : last.state === "failed" ? "bad" : last.state === "waiting_gate" ? "warn" : ""}>{last.state}</Pill>
            </div>
          )}
        </div>
        <div className="rd-kits-card">
          <strong>Settings</strong>
          {Object.entries(kit.options).map(([name, choice]) => (
            <div className="rd-kits-line" key={name}><div>{name} <span className="rd-kits-mono">{choice}</span><div className="rd-kits-note">Chosen at install; reinstall to change it.</div></div></div>
          ))}
          {kit.harnesses.includes("codex") && byKind.hook && (
            <div className="rd-kits-line"><div>Codex hooks<div className="rd-kits-note">Codex runs a project's hooks only after you trust the folder in Codex. DuckTerm never does that for you.</div></div><Pill tone="warn">waiting for trust</Pill></div>
          )}
          {Object.keys(kit.options).length === 0 && !(kit.harnesses.includes("codex") && byKind.hook) && <div className="rd-kits-sub">Nothing to set for this kit.</div>}
        </div>
      </div>
      {Object.keys(kit.skills ?? {}).length > 0 && (
        <div className="rd-kits-card">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><strong>Skills</strong><span className="rd-kits-sub">click one to see what it does · invoke with /name in a session</span></div>
          {Object.entries(kit.skills).map(([name, skill]) => (
            <details className="rd-kits-skill" key={name}>
              <summary><span className="rd-kits-mono">/{name}</span>{skill.effects && <Pill>{skill.effects}</Pill>}</summary>
              <div className="rd-kits-note">{skill.description || "No description in its SKILL.md."}</div>
            </details>
          ))}
        </div>
      )}
      <details className="rd-kits-card" style={{ display: "block" }}>
        <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: "0.8125rem" }}>What else this kit installed <span className="rd-kits-sub" style={{ fontWeight: 400 }}>· {counts} · {kit.entries} entries</span></summary>
        {Object.entries(byKind).map(([kind, names]) => (
          <div className="rd-kits-line" key={kind}><span>{kind === "team" ? "team" : kind}</span><span className="rd-kits-mono" style={{ color: "var(--text-soft)", textAlign: "right" }}>{names.join(" · ")}</span></div>
        ))}
      </details>
    </>
  );
}
