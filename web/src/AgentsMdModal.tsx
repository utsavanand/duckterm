import { useEffect, useState } from "react";
import { sessionFetch } from "./hostTransport";
import { authHeaders } from "./api";
import { useToast } from "./ui";
import "./agentsRules.css";
import { useFeature } from "./edition";

interface Example { situation: string; do: string; avoid?: string; explanation?: string }
export interface Rule {
  id: string; text: string; scope: string; status: "active" | "candidate" | "rejected" | "retired";
  source: string; evidence: number; added: string; heading: string; note: string;
  title: string; explanation: string; when: string; examples: Example[]; exceptions: string[];
  revision: number; source_refs: string[];
}
type Content = Pick<Rule, "title" | "text" | "scope" | "explanation" | "when" | "examples" | "exceptions" | "heading">;
interface Proposal {
  id: string; status: "pending" | "accepted" | "rejected"; scope_override?: string;
  claim: string; memory_kind: string; source_refs: string[]; action: string; reason: string;
  target: { rule_id: string; base_revision: number } | null;
  changes: { rule?: Content; fields?: Partial<Content>; example?: Example } | null;
  context: { destination: string; text: string } | null;
}
interface Catalog { rules: Rule[]; proposals: Proposal[]; version: string; drift: boolean; text: string; rendered_text: string; managed: boolean }
const labels: Record<string, string> = { create_rule: "New rule", revise_rule: "Update existing rule", add_example: "Add example", attach_evidence: "Link evidence", no_change: "Already covered", retain_context: "Keep in context", needs_review: "Needs your review", ignore: "No memory change" };
const applicable = new Set(["create_rule", "revise_rule", "add_example", "attach_evidence"]);
const emptyContent = (): Content => ({ title: "", text: "", scope: "all", explanation: "", when: "", examples: [], exceptions: [], heading: "" });
const name = (r: Rule) => r.title || r.text.split("\n")[0];
const harnessName = (s: string) => s === "all" ? "All harnesses" : s === "codex" ? "Codex" : s === "claude-code" ? "Claude Code" : s;

function RuleContent({ rule }: { rule: Partial<Content> }) {
  return <div className="ar-content">
    {rule.title && <h3>{rule.title}</h3>}{rule.text && <p>{rule.text}</p>}
    {rule.when && <p><strong>When:</strong> {rule.when}</p>}
    {rule.explanation && <p><strong>Why:</strong> {rule.explanation}</p>}
    {!!rule.exceptions?.length && <div><strong>Exceptions</strong><ul>{rule.exceptions.map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
    {rule.examples?.map((e, i) => <div className="ar-example" key={i}><strong>{e.situation}</strong><p><b>Do:</b> {e.do}</p>{e.avoid && <p><b>Avoid:</b> {e.avoid}</p>}{e.explanation && <p>{e.explanation}</p>}</div>)}
  </div>;
}

export function AgentsMdModal({ dir, sessionKey = "", onClose }: { dir: string; sessionKey?: string; onClose: () => void }) {
  const toast = useToast();
  const suggestions = useFeature("rule_suggestions");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [tab, setTab] = useState("rules");
  const [query, setQuery] = useState("");
  const [audience, setAudience] = useState("any");
  const [status, setStatus] = useState("active");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [replaceFile, setReplaceFile] = useState(false);
  const [editor, setEditor] = useState<{ id: string | null; content: Content } | null>(null);
  const [reload, setReload] = useState(0);
  const pathName = dir.split("/").filter(Boolean).pop() || dir;
  const adopt = (d: Catalog) => { setCatalog(d); setRules(d.rules); setProposals(d.proposals || []); setDirty(false); setReplaceFile(false); };
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(""); setCatalog(null); setEditor(null);
    sessionFetch(sessionKey, `/agents-md?dir=${encodeURIComponent(dir)}`).then(async r => {
      const d = await r.json(); if (!r.ok) throw new Error(d.error || "Could not load rules"); return d as Catalog;
    }).then(d => { if (!cancelled) adopt(d); }).catch(e => { if (!cancelled) setError(String(e.message)); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [dir, sessionKey, reload]);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", prevent); return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);

  const post = async (url: string, body: unknown) => {
    const response = await sessionFetch(sessionKey, url, { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body) });
    const d = await response.json(); if (!response.ok) throw new Error(d.error || `Request failed (${response.status})`); return d;
  };
  async function save() {
    if (!catalog || loading || busy) return;
    setBusy(true); setError("");
    try { const d = await post("/agents-md", { dir, rules, proposals, version: catalog.version, reconcile_drift: replaceFile }); adopt(d); toast("Rules saved — changes apply when instructions are next loaded"); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function suggest(draft?: Content) {
    if (!catalog || busy || !suggestions) return;
    setBusy(true); setError("");
    try {
      const d = await post("/agents-md/suggest", { dir, rules, ...(draft ? { draft } : {}) });
      setProposals(current => [...current, ...(d.proposals as Proposal[]).filter(p => !current.some(c => c.id === p.id))]);
      setDirty(true); setTab("suggestions"); setEditor(null);
      if (!d.proposals.length) toast("No new memory changes found");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const changeRule = (id: string, changes: Partial<Rule>) => { setRules(rs => rs.map(r => r.id === id ? { ...r, ...changes } : r)); setDirty(true); };
  const review = (id: string, decision: Proposal["status"]) => { setProposals(ps => ps.map(p => p.id === id ? { ...p, status: decision } : p)); setDirty(true); };
  const close = () => { if (!dirty || window.confirm("Discard unsaved rule changes?")) onClose(); };
  const filtered = rules.filter(r => r.status === status && (audience === "any" || r.scope === audience) && `${r.title} ${r.text} ${r.explanation} ${r.heading} ${JSON.stringify(r.examples)}`.toLowerCase().includes(query.toLowerCase()));
  const groups = [...new Set(filtered.map(r => r.heading || "General"))];
  const scopes = [...new Set(["all", "codex", "claude-code", ...rules.map(r => r.scope)])];
  const pending = proposals.filter(p => p.status === "pending").length + rules.filter(r => r.status === "candidate").length;
  const setField = <K extends keyof Content>(field: K, value: Content[K]) => setEditor(e => e && ({ ...e, content: { ...e.content, [field]: value } }));
  const canSave = !!catalog && !loading && !busy && dirty && (!catalog.drift || replaceFile) && (catalog.managed || !catalog.text);

  return <section className="ar-page" aria-label="AGENTS.md rules page">
    <header className="ar-header"><button onClick={close} disabled={busy}>← Back to session</button><span>{pathName} / <strong>AGENTS.md</strong></span><span className="ar-save-state">{dirty ? "Unsaved changes" : "Saved rules"}</span><button className="ar-primary" onClick={save} disabled={!canSave}>{busy ? "Working…" : "Save"}</button></header>
    <div className="ar-layout"><aside className="ar-sidebar"><span className="ar-label">Rules library</span><h2>{pathName}</h2><p>Procedural memory</p><span className="ar-label">Where it applies</span><div className="ar-selected">This directory</div><p className="ar-small">Global, named-agent, and session scopes are not available yet.</p><label className="ar-label" htmlFor="ar-audience">Who it targets</label><select id="ar-audience" value={audience} onChange={e => { setAudience(e.target.value); setTab("rules"); }}><option value="any">All targets</option>{scopes.map(s => <option key={s} value={s}>{harnessName(s)}</option>)}</select><label className="ar-label" htmlFor="ar-status">Rule status</label><select id="ar-status" value={status} onChange={e => { setStatus(e.target.value); setTab("rules"); }}><option value="active">Active</option><option value="candidate">Candidates</option><option value="rejected">Rejected</option><option value="retired">Retired</option></select>{tab === "rules" && <nav aria-label="Rule sections"><span className="ar-label">On this page</span>{groups.map((g, i) => <a href={`#ar-section-${i}`} key={g}>{g}</a>)}</nav>}</aside>
    <main className="ar-main"><div className="ar-heading"><div><h1>AGENTS.md</h1><p>How your agents should behave. Every rule, with its scope and examples.</p></div><button className="ar-primary" disabled={loading || busy || !catalog || (!catalog.managed && !!catalog.text)} onClick={() => setEditor({ id: null, content: emptyContent() })}>＋ Add rule</button></div><p className="ar-path">{dir}/AGENTS.md</p>
      <nav className="ar-tabs" aria-label="Rule views">{[["rules", `Rules ${rules.filter(r => r.status === "active").length}`], ["suggestions", `Suggestions ${pending}`], ["file", "File preview"]].map(([id, label]) => <button key={id} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>{label}</button>)}</nav>
      {loading && <p role="status">Loading rules…</p>}{error && <div className="ar-error" role="alert">{error} {!catalog && <button onClick={() => setReload(n => n + 1)}>Retry</button>}</div>}
      {catalog?.drift && <div className="ar-notice"><strong>AGENTS.md has changes outside the rule editor.</strong><p>Compare the file with stored rules before replacing it. A backup will be kept.</p><button onClick={() => setTab("file")}>Compare files</button>{tab === "file" && <label><input type="checkbox" checked={replaceFile} onChange={e => { setReplaceFile(e.target.checked); setDirty(true); }} /> I reviewed the differences. Replace the file and keep a backup.</label>}</div>}
      {catalog && !catalog.managed && catalog.text && <div className="ar-notice">This is a handwritten AGENTS.md. Read it in File preview. Structured editing requires an explicit migration so no instructions are lost.<button onClick={() => setTab("file")}>Read file</button></div>}
      {tab === "rules" && <><div className="ar-tools"><input aria-label="Search rules" placeholder="Search rules…" value={query} onChange={e => setQuery(e.target.value)} /><span>{filtered.length} rules</span>{suggestions && <button onClick={() => suggest()} disabled={!catalog || loading || busy}>Suggest from corrections</button>}</div><div className="ar-notice">Rules apply in this directory. Harness labels narrow who receives them.</div>{!loading && !filtered.length && <p className="ar-empty">No matching rules.</p>}{groups.map((g, i) => <section key={g}><h2 id={`ar-section-${i}`}>{g}</h2>{filtered.filter(r => (r.heading || "General") === g).map(r => <article className="ar-rule" key={r.id}><div className="ar-rule-heading"><span className="ar-pill">{harnessName(r.scope)}</span><span className="ar-small">Revision {r.revision}</span><button onClick={() => setEditor({ id: r.id, content: { ...emptyContent(), ...r } })} disabled={busy}>Edit</button></div><RuleContent rule={r} />{r.status === "candidate" && <div className="ar-actions"><button onClick={() => changeRule(r.id, { status: "active" })}>Accept</button><button onClick={() => changeRule(r.id, { status: "rejected" })}>Reject</button></div>}{r.status === "active" && <button className="ar-text-button" onClick={() => changeRule(r.id, { status: "retired" })}>Retire rule</button>}{(r.status === "retired" || r.status === "rejected") && <button onClick={() => changeRule(r.id, { status: "candidate" })}>Restore for review</button>}</article>)}</section>)}</>}
      {tab === "suggestions" && <><div className="ar-tools"><div><h2>Review before applying</h2><p>Consolidate behavior; keep episodes in context.</p></div>{suggestions && <button onClick={() => suggest()} disabled={!catalog || busy}>Review corrections</button>}</div>{!proposals.length && <p className="ar-empty">No reconciliation proposals yet.</p>}{proposals.map(p => {
        const target = rules.find(r => r.id === p.target?.rule_id);
        return <article className="ar-proposal" key={p.id}><div className="ar-rule-heading"><strong>{labels[p.action] || p.action}</strong><span className="ar-pill">{p.memory_kind}</span><span>{p.status === "accepted" && dirty ? "Accepted · save to apply" : p.status}</span></div><h3>{p.claim}</h3><p>{p.reason}</p>{target && <p className="ar-small">Existing rule: {name(target)} · revision {p.target?.base_revision}</p>}{p.changes?.rule && <><RuleContent rule={p.changes.rule} /><label className="ar-proposal-scope">Applies in this directory to <select aria-label="Suggested rule harness" disabled={p.status !== "pending" || busy} value={p.scope_override || p.changes.rule.scope} onChange={e => { setProposals(ps => ps.map(x => x.id === p.id ? { ...x, scope_override: e.target.value } : x)); setDirty(true); }}>{[...new Set([...scopes, p.changes.rule.scope])].map(s => <option key={s} value={s}>{harnessName(s)}</option>)}</select></label></>}{p.changes?.fields && <div className="ar-diff"><div><h4>Before</h4><RuleContent rule={target || {}} /></div><div><h4>Proposed revision</h4><RuleContent rule={{ ...target, ...p.changes.fields }} /><p>Scope: {harnessName(p.changes.fields.scope || target?.scope || "all")}</p></div></div>}{p.changes?.example && <RuleContent rule={{ examples: [p.changes.example] }} />}{p.context && <div className="ar-notice"><p>{p.context.text}</p><small>Retained as an observation linked to the original source. This does not install a rule or create a checkpoint.</small></div>}<details><summary>Source evidence ({p.source_refs.length})</summary>{p.source_refs.map(ref => <div className="ar-source" key={ref}>{ref}</div>)}</details>{p.status === "pending" && <div className="ar-actions"><button onClick={() => review(p.id, "rejected")} disabled={busy}>Dismiss</button>{applicable.has(p.action) && <button className="ar-primary" disabled={busy} onClick={() => review(p.id, "accepted")}>Accept change</button>}</div>}{p.status !== "pending" && catalog?.proposals.find(x => x.id === p.id)?.status !== p.status && <button onClick={() => review(p.id, "pending")} disabled={busy}>Undo review</button>}</article>;
      })}</>}
      {tab === "file" && <div className="ar-diff"><div><h2>Current AGENTS.md</h2><pre>{catalog?.text || "No file yet."}</pre></div>{catalog?.drift && <div><h2>Stored rules render</h2><pre>{catalog.rendered_text}</pre></div>}</div>}
    </main></div>
    {editor && <div className="ar-editor-backdrop"><section className="ar-editor" role="dialog" aria-modal="true" aria-label={editor.id ? "Edit rule" : "Add rule"}><h2>{editor.id ? "Edit rule" : "Add rule"}</h2><p>{editor.id ? "Save a reviewed revision of this rule." : suggestions ? "Compare this instruction with existing rules before adding it." : "Review existing rules before adding this instruction. Automatic comparison is unavailable in this edition."}</p><label>Title<input autoFocus maxLength={120} value={editor.content.title} onChange={e => setField("title", e.target.value)} /></label><label>Instruction<textarea className="rd-rule-edit" rows={4} value={editor.content.text} onChange={e => setField("text", e.target.value)} /></label><div className="ar-form-row"><label>Harness<select value={editor.content.scope} onChange={e => setField("scope", e.target.value)}>{[...new Set([...scopes, editor.content.scope])].map(s => <option key={s} value={s}>{harnessName(s)}</option>)}</select></label><label>Section<input value={editor.content.heading} onChange={e => setField("heading", e.target.value)} /></label></div><label>Why<textarea rows={2} value={editor.content.explanation} onChange={e => setField("explanation", e.target.value)} /></label><label>When<input value={editor.content.when} onChange={e => setField("when", e.target.value)} /></label><label>Exceptions (one per line)<textarea rows={2} value={editor.content.exceptions.join("\n")} onChange={e => setField("exceptions", e.target.value.split("\n").filter(Boolean))} /></label><h3>Examples</h3>{editor.content.examples.map((example, i) => <fieldset key={i}><legend>Example {i + 1}</legend>{(["situation", "do", "avoid", "explanation"] as const).map(field => <label key={field}>{({ situation: "Situation", do: "Do", avoid: "Avoid", explanation: "Explanation" })[field]}<textarea rows={2} value={example[field] || ""} onChange={e => setField("examples", editor.content.examples.map((x, n) => n === i ? { ...x, [field]: e.target.value } : x))} /></label>)}<button onClick={() => setField("examples", editor.content.examples.filter((_, n) => n !== i))}>Remove example</button></fieldset>)}<button disabled={editor.content.examples.length >= 8} onClick={() => setField("examples", [...editor.content.examples, { situation: "", do: "" }])}>Add example</button><div className="ar-actions"><button disabled={busy} onClick={() => setEditor(null)}>Cancel</button><button className="ar-primary" disabled={busy || !editor.content.text.trim() || !editor.content.title.trim()} onClick={() => {
      const content = { ...editor.content, examples: editor.content.examples.map(e => Object.fromEntries(Object.entries(e).filter(([, v]) => v.trim())) as unknown as Example) };
      if (editor.id) { const { title, text, scope, explanation, when, examples, exceptions, heading } = content; changeRule(editor.id, { title, text, scope, explanation, when, examples, exceptions, heading }); setEditor(null); }
      else { const { title, text, scope, explanation, when, examples, exceptions, heading } = content;
        if (suggestions) void suggest({ title, text, scope, explanation, when, examples, exceptions, heading });
        else {
          if (rules.some(r => r.scope === scope && r.text.trim().toLowerCase().replace(/\s+/g, " ") === text.trim().toLowerCase().replace(/\s+/g, " "))) { setError("This instruction already exists. Edit the existing rule."); return; }
          setRules(rs => [...rs, { id: crypto.randomUUID(), title, text, scope, explanation, when, examples, exceptions, heading, status: "active", source: "manual", evidence: 1, added: new Date().toISOString().slice(0, 10), note: "", revision: 1, source_refs: [] }]);
          setDirty(true); setEditor(null);
        }
      }
    }}>{busy ? "Reviewing…" : editor.id ? "Keep changes" : suggestions ? "Compare with existing rules" : "Keep new rule"}</button></div></section></div>}
  </section>;
}
