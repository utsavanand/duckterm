import { useRef, useState } from "react";
import type { ReviewCard, ReviewHeader } from "../api";
import { html } from "../render";
import { BUCKET_LABELS } from "./diffModel";
import { KIND_LABELS, type BehaviourData, type IntentData, type QuestionsData, type ReleaseData, type RiskData, type WalkthroughData } from "./cardModel";
import { useMermaid } from "./useMermaid";

export interface CardActions {
  openFile: (path: string, hunk?: number) => void;
  ask: (text: string) => void;
  send: (card: string, items: string[]) => Promise<void>;
  header: ReviewHeader;
}

export function PathLink({ path, hunk, actions }: { path: string; hunk?: number; actions: CardActions }) {
  return <button className="rd-card-path" onClick={() => actions.openFile(path, hunk)}>{path}</button>;
}

export function IntentCard({ data, actions }: { data: IntentData; actions: CardActions }) {
  const box = useRef<HTMLDivElement>(null);
  useMermaid(box, data.diagram);
  return <div ref={box} className="rd-card-body">
    <div className="rd-card-prose" dangerouslySetInnerHTML={{ __html: html(data.summary) }} />
    <h4>Claims</h4>
    <table className="rd-card-table"><thead><tr><th>Claim</th><th>In diff</th><th>Evidence</th></tr></thead><tbody>
      {data.claims.map((claim, index) => <tr key={index}>
        <td>{claim.claim}{claim.note && <small> · {claim.note}</small>}</td>
        <td><span className={`rd-card-flag is-${claim.in_diff}`}>{claim.in_diff === "true" ? "yes" : claim.in_diff === "false" ? "no" : "partial"}</span></td>
        <td>{claim.evidence.map(path => <PathLink key={path} path={path} actions={actions} />)}{claim.evidence_missing?.length ? <small> (not in diff: {claim.evidence_missing.join(", ")})</small> : null}</td>
      </tr>)}
    </tbody></table>
    {data.unclaimed.length > 0 && <>
      <h4>Present but not claimed</h4>
      <ul className="rd-card-list">{data.unclaimed.map((item, index) => <li key={index}><PathLink path={item.path} actions={actions} /> {item.what}</li>)}</ul>
    </>}
    {data.diagram && <><h4>Changed modules</h4><pre className="rd-mermaid"><code>{data.diagram}</code></pre></>}
  </div>;
}

export function WalkthroughCard({ data, actions }: { data: WalkthroughData; actions: CardActions }) {
  return <div className="rd-card-body">
    <p className="rd-card-meta">Reading budget: {(["high", "read", "skim"] as const).map(bucket => `${BUCKET_LABELS[bucket]} ${data.minutes[bucket]} min`).join(" · ")}</p>
    <ol className="rd-card-stops">{data.stops.map((stop, index) => <li key={index}>
      <button className="rd-card-stop" onClick={() => actions.openFile(stop.path, stop.hunk)}>
        <strong>{stop.title}</strong><span className={`rd-card-bucket is-${stop.bucket}`}>{BUCKET_LABELS[stop.bucket]}</span>
        <code>{stop.path} · hunk {stop.hunk}</code>
        <span>{stop.why}</span>
      </button>
    </li>)}</ol>
  </div>;
}

export function RiskCard({ data, actions }: { data: RiskData; actions: CardActions }) {
  const total = Math.max(1, data.files.reduce((sum, file) => sum + file.lines, 0));
  return <div className="rd-card-body">
    <div className="rd-treemap" aria-label="Changed lines by file and bucket">
      {(["high", "read", "skim"] as const).map(bucket => {
        const files = data.files.filter(file => file.bucket === bucket);
        if (files.length === 0) return null;
        const lines = files.reduce((sum, file) => sum + file.lines, 0);
        return <div key={bucket} className={`rd-treemap-row is-${bucket}`} style={{ flexGrow: Math.max(lines, 1) }}>
          <span className="rd-treemap-label">{BUCKET_LABELS[bucket]} · {files.length} · {Math.round(lines / total * 100)}%</span>
          <div className="rd-treemap-cells">{files.map(file => <button key={file.path} className="rd-treemap-cell" style={{ flexGrow: Math.max(file.lines, 1) }} title={`${file.path} · ${file.lines} lines`} onClick={() => actions.openFile(file.path)}>{file.path.split("/").pop()}</button>)}</div>
        </div>;
      })}
    </div>
    {data.reasons.length > 0 && <><h4>Why these are high risk</h4>
      <ul className="rd-card-list">{data.reasons.map(item => <li key={item.path}><PathLink path={item.path} actions={actions} /> {item.reason}</li>)}</ul></>}
    <h4>Signals</h4>
    <table className="rd-card-table"><thead><tr><th>File</th><th>Bucket</th><th>Rules</th><th>Lines</th><th>Reverts (180 d)</th></tr></thead><tbody>
      {data.files.map(file => <tr key={file.path}>
        <td><PathLink path={file.path} actions={actions} /></td>
        <td><span className={`rd-card-bucket is-${file.bucket}`}>{BUCKET_LABELS[file.bucket]}</span></td>
        <td>{file.rules.map(rule => rule.reason).join("; ") || "—"}{file.hotspot && ` · ${file.hotspot}`}</td>
        <td>{file.lines}</td><td>{file.reverts}</td>
      </tr>)}
    </tbody></table>
  </div>;
}

export function BehaviourCard({ data, actions }: { data: BehaviourData; actions: CardActions }) {
  if (data.rows.length === 0) return <div className="rd-card-body"><p className="rd-prs-empty">The model found no user-visible behaviour change; the diff reads as a refactor.</p></div>;
  return <div className="rd-card-body">
    <table className="rd-card-table"><thead><tr><th>Situation</th><th>Before</th><th>After</th><th>Who notices</th><th>Regression risk</th><th>Evidence</th></tr></thead><tbody>
      {data.rows.map((row, index) => <tr key={index}>
        <td>{row.situation}</td><td>{row.before}</td><td>{row.after}</td><td>{row.who_notices}</td>
        <td><span className={`rd-card-flag is-${row.regression_risk}`}>{row.regression_risk}</span></td>
        <td>{row.evidence.map(path => <PathLink key={path} path={path} actions={actions} />)}</td>
      </tr>)}
    </tbody></table>
  </div>;
}

export function ReleaseCard({ data, actions }: { data: ReleaseData; actions: CardActions }) {
  return <div className="rd-card-body">
    <p className={`rd-card-verdict is-${data.low_risk === "true" ? "ok" : data.low_risk === "conditional" ? "warn" : "danger"}`}><strong>{data.verdict}</strong>{data.low_risk !== "conditional" && <span> · {data.condition}</span>}</p>
    <ul className="rd-card-checks">{data.checks.map(check => <li key={check.name} className={`is-${check.status}`}>
      <span className="rd-card-check-status">{check.status}</span>
      <span><strong>{check.name}</strong>{check.note && ` · ${check.note}`}{check.hits.length > 0 && <span className="rd-card-hits">{check.hits.map(hit => hit.includes(" ") || !hit.includes("/") ? <code key={hit}>{hit}</code> : <PathLink key={hit} path={hit.split(":")[0]} actions={actions} />)}</span>}</span>
    </li>)}</ul>
    <p className="rd-card-meta">Low-risk rule from AGENTS.md: no schema, data, security or updater change, QA and gate green.</p>
  </div>;
}

export function QuestionsCard({ card, data, actions }: { card: ReviewCard; data: QuestionsData; actions: CardActions }) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const session = actions.header.session;
  async function send(items: string[]) {
    setSending(true); setError("");
    try { await actions.send(card.id, items); } catch (cause) { setError((cause as Error).message); } finally { setSending(false); }
  }
  const kinds = (["unverified", "confirm", "cleanup", "manual"] as const).map(kind => ({ kind, items: data.items.filter(item => item.kind === kind) })).filter(group => group.items.length > 0);
  return <div className="rd-card-body">
    {data.items.length === 0 && <p className="rd-prs-empty">Nothing left open by the other cards.</p>}
    {kinds.map(group => <section key={group.kind}>
      <h4>{KIND_LABELS[group.kind]}</h4>
      <ul className="rd-card-questions">{group.items.map((item, index) => <li key={index}>
        <span>{item.text} {item.paths.map(path => <PathLink key={path} path={path} actions={actions} />)}</span>
        <span className="rd-card-question-actions">
          <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => actions.ask(item.text)}>Ask</button>
          {session && <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={sending} onClick={() => void send([item.text])}>Send</button>}
        </span>
      </li>)}</ul>
    </section>)}
    {session && data.items.length > 0 && <div className="rd-card-send"><button className="rd-btn rd-btn-primary rd-btn-sm" disabled={sending} onClick={() => void send(data.items.map(item => `${item.text}${item.paths.length ? ` (${item.paths.join(", ")})` : ""}`))}>{sending ? "Sending…" : `Send all to ${session.name}`}</button>
      {card.sent?.length ? <small>Sent {card.sent.length === 1 ? "once" : `${card.sent.length} times`}; last message {card.sent[card.sent.length - 1].message_id.slice(0, 8)}</small> : null}</div>}
    {!session && data.items.length > 0 && <p className="rd-card-meta">No session on this computer owns {actions.header.branch}, so there is nowhere to send these.</p>}
    {error && <p className="rd-prs-note" role="alert">{error}</p>}
  </div>;
}
