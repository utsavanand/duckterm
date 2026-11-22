import { useEffect, useRef, useState } from "react";
import { api, OracleExchange } from "./api";
import { html } from "./render";

export type ChatSession = { key: string; name: string };

const unescape = (text: string) => text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
const attr = (text: string) => text.replace(/&/g, "&amp;").replace(/"/g, "&quot;");

// Answers name sessions in bold; a bold name that is a real session becomes a
// chip that opens it.
export function withSessionChips(answer: string, sessions: ChatSession[]): string {
  const byName = new Map(sessions.map(s => [s.name, s.key]));
  return answer.replace(/<strong>([^<]+)<\/strong>/g, (whole, name: string) => {
    const key = byName.get(unescape(name));
    return key ? `<button type="button" class="rd-home-cite" data-session="${attr(key)}">${name}</button>` : whole;
  });
}

const SUGGESTIONS = ["Which sessions are waiting on me?", "What is each session working on right now?", "Are any two sessions working on the same thing?"];

// Home's chat. Answers come from the same /fleet/ask conversation Oracle keeps,
// so history carries over; agents' questions to the owner live in Needs you,
// not here. Autopilot joins the scope once the factory has data to read.
export function HomeChat({ sessions, onOpenTerminal }: { sessions: ChatSession[]; onOpenTerminal: (key: string) => void }) {
  const [log, setLog] = useState<OracleExchange[]>([]);
  const [q, setQ] = useState(""), [pending, setPending] = useState<string | null>(null), [error, setError] = useState("");
  // Until Autopilot reports data, Everything and Teams read the same sessions.
  const [scope, setScope] = useState<"Everything" | "Teams">("Everything");
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let stale = false;
    api.oracleChat().then(r => { if (!stale) setLog(r.messages); }, (e: Error) => { if (!stale) setError(`Could not load the conversation: ${e.message}`); });
    return () => { stale = true; };
  }, []);
  useEffect(() => { const el = logRef.current; if (el) el.scrollTop = el.scrollHeight; }, [log, pending, error]);
  async function ask(text: string) {
    const question = text.trim();
    if (!question || pending !== null) return;
    setPending(question); setQ(""); setError("");
    try { const r = await api.fleetAsk(question); setLog(l => [...l, r.exchange]); }
    catch (e) { setError(`DuckTerm could not answer: ${(e as Error).message}`); setQ(question); }
    finally { setPending(null); }
  }
  async function clear() {
    try { setLog((await api.clearOracleChat()).messages); }
    catch (e) { setError(`Could not clear: ${(e as Error).message}`); }
  }
  return <div className="rd-home-chat">
    <div className="rd-home-scope" aria-label="What answers draw on">
      {(["Everything", "Teams"] as const).map(name => <button key={name} type="button" className={`rd-home-scope-chip${scope === name ? " on" : ""}`} aria-pressed={scope === name} onClick={() => setScope(name)}>{name}</button>)}
      <button type="button" className="rd-home-scope-chip off" disabled title="Autopilot isn't connected yet">Autopilot</button>
      {log.length > 0 && <button className="rd-home-clear" onClick={() => void clear()}>Clear</button>}
    </div>
    <div className="rd-home-log" ref={logRef} onClick={event => {
      const chip = (event.target as HTMLElement).closest<HTMLElement>(".rd-home-cite");
      if (chip?.dataset.session) onOpenTerminal(chip.dataset.session);
    }}>
      {log.length === 0 && pending === null && <p className="rd-home-chat-intro">Ask about anything your teams are doing: who is stuck, what each session is working on, what needs you.</p>}
      {log.map(exchange => <div key={exchange.at} className="rd-home-exchange">
        <div className="rd-home-q">{exchange.q}</div>
        <div className="rd-home-a rd-msg-text" dangerouslySetInnerHTML={{ __html: withSessionChips(html(exchange.a), sessions) }} />
      </div>)}
      {pending !== null && <div className="rd-home-exchange"><div className="rd-home-q">{pending}</div><div className="rd-home-thinking" role="status"><span className="rd-spinner" /> Reading your sessions…</div></div>}
      {error && <p className="rd-oracle-error" role="alert">{error}</p>}
    </div>
    <div className="rd-home-suggest">{SUGGESTIONS.map(s => <button key={s} disabled={pending !== null} onClick={() => void ask(s)}>{s}</button>)}</div>
    <form className="rd-home-compose" onSubmit={event => { event.preventDefault(); void ask(q); }}>
      <textarea aria-label="Ask DuckTerm" rows={1} value={q} placeholder="Ask about your teams, the factory, PRs or spend…" onChange={event => setQ(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void ask(q); } }} />
      <button className="rd-btn rd-btn-primary rd-btn-sm" disabled={pending !== null}>Ask</button>
    </form>
  </div>;
}
