import { useEffect, useRef, useState } from "react";
import { api, OracleExchange } from "./api";
import { useEdition } from "./edition";
import { modelHtml } from "./render";

export type ChatSession = { key: string; name: string; folder?: string; state?: string };

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
// A reply in one of these states will not change, so the chat stops checking.
const SETTLED = new Set(["answered", "declined", "expired", "cancelled"]);
const REPLY_POLL_MS = 15_000;
const requestKey = () => crypto.randomUUID().replace(/-/g, "");

// Home's chat. Answers come from the same /fleet/ask conversation Oracle keeps,
// so history carries over; agents' questions to the owner live in Needs you,
// not here. "@name question" goes to that session's inbox through Oracle, and
// its answer comes back under the question.
export function HomeChat({ sessions, onOpenTerminal }: { sessions: ChatSession[]; onOpenTerminal: (key: string) => void }) {
  const messaging = useEdition().has("oracle_messages");
  const [log, setLog] = useState<OracleExchange[]>([]);
  const [q, setQ] = useState(""), [pending, setPending] = useState<string | null>(null), [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement>(null), input = useRef<HTMLTextAreaElement>(null);
  // The session the owner picked from the @ list. Names repeat across folders
  // and may hold spaces, so a pick is held by key, never looked up by name.
  const [picked, setPicked] = useState<ChatSession | null>(null);
  useEffect(() => {
    let stale = false;
    api.oracleChat().then(r => { if (!stale) setLog(r.messages); }, (e: Error) => { if (!stale) setError(`Could not load the conversation: ${e.message}`); });
    return () => { stale = true; };
  }, []);
  useEffect(() => { const el = logRef.current; if (el) el.scrollTop = el.scrollHeight; }, [log, pending, error]);
  // "/" jumps to the chat, unless the owner is typing somewhere (a terminal is a
  // textarea too), a dialog is open, or Home is hidden under a review.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
      if (target.closest("input, textarea, select, [contenteditable], [role=dialog]") || !input.current || input.current.closest("[hidden]")) return;
      event.preventDefault();
      input.current.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const waiting = log.some(e => e.key && !SETTLED.has(e.reply?.status ?? ""));
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => { api.oracleChat().then(r => setLog(r.messages), () => undefined); }, REPLY_POLL_MS);
    return () => clearInterval(timer);
  }, [waiting]);

  // While the owner types "@na", offer the sessions whose names match.
  const typing = messaging ? /^@(\S*)$/.exec(q)?.[1] : undefined;
  const matches = typing === undefined ? [] : sessions.filter(s => s.name.toLowerCase().includes(typing.toLowerCase())).slice(0, 6);
  function pick(session: ChatSession) { setPicked(session); setQ(`@${session.name} `); input.current?.focus(); }

  async function ask(text: string) {
    const question = text.trim();
    if (!question || pending !== null) return;
    let to: ChatSession | undefined, body = "";
    if (messaging && picked && question.startsWith(`@${picked.name} `)) {
      to = sessions.find(s => s.key === picked.key);
      if (!to) { setError(`${picked.name} is no longer running. Nothing was sent.`); return; }
      body = question.slice(picked.name.length + 2).trim();
    } else if (messaging && question.startsWith("@")) {
      // Typed without the list: only a name that is one session's, whole.
      const mention = /^@(\S+)\s+([\s\S]+)$/.exec(question);
      const named = mention ? sessions.filter(s => s.name.toLowerCase() === mention[1].toLowerCase()) : [];
      if (!mention || named.length !== 1) {
        setError(named.length > 1 ? `${named.length} sessions are named "${mention![1]}". Pick the one you mean from the @ list. Nothing was sent.`
          : `No session is named "${mention ? mention[1] : question.slice(1)}". Pick one from the @ list. Nothing was sent.`);
        return;
      }
      [to] = named; body = mention[2].trim();
    }
    if (to && !body) { setError(`Write a question for ${to.name}.`); return; }
    setPending(question); setQ(""); setError(""); setPicked(null);
    try {
      if (to) {
        const { exchange } = await api.messageSession(to.key, body, "inbox", requestKey(), question);
        if (exchange) setLog(l => [...l, exchange]);
      } else {
        const r = await api.fleetAsk(question);
        setLog(l => [...l, r.exchange]);
      }
    } catch (e) { setError(`${to ? `Could not send to ${to.name}` : "DuckTerm could not answer"}: ${(e as Error).message}`); setQ(question); }
    finally { setPending(null); }
  }
  async function clear() {
    try { setLog((await api.clearOracleChat()).messages); }
    catch (e) { setError(`Could not clear: ${(e as Error).message}`); }
  }
  return <div className="rd-home-chat">
    {log.length > 0 && <button className="rd-home-clear" onClick={() => void clear()}>Clear</button>}
    <div className="rd-home-log" ref={logRef} onClick={event => {
      const chip = (event.target as HTMLElement).closest<HTMLElement>(".rd-home-cite");
      if (chip?.dataset.session) onOpenTerminal(chip.dataset.session);
    }}>
      {log.length === 0 && pending === null && <p className="rd-home-chat-intro">Ask about anything your teams are doing: who is stuck, what each session is working on, what needs you.{messaging && " Start with @ to ask one session directly."}</p>}
      {log.map(exchange => <div key={exchange.at} className="rd-home-exchange">
        <div className="rd-home-q">{exchange.q}</div>
        <div className="rd-home-a rd-msg-text" dangerouslySetInnerHTML={{ __html: withSessionChips(modelHtml(exchange.a), sessions) }} />
        {exchange.key && exchange.to && <Reply exchange={exchange} />}
      </div>)}
      {pending !== null && <div className="rd-home-exchange"><div className="rd-home-q">{pending}</div><div className="rd-home-thinking" role="status"><span className="rd-spinner" /> {pending.startsWith("@") && messaging ? "Sending…" : "Reading your sessions…"}</div></div>}
      {error && <p className="rd-oracle-error" role="alert">{error}</p>}
    </div>
    <div className="rd-home-suggest">{SUGGESTIONS.map(s => <button key={s} disabled={pending !== null} onClick={() => void ask(s)}>{s}</button>)}
      <button disabled title="Coming soon: DuckTerm builds views you describe">Make me a view of failed runs <span className="rd-home-soon">Soon</span></button></div>
    <div className="rd-home-compose-wrap">
      {matches.length > 0 && <div className="rd-home-mention" role="listbox" aria-label="Ask a session">
        <small>Oracle sends your question to</small>
        {matches.map(s => <button key={s.key} type="button" role="option" aria-selected="false" onClick={() => pick(s)}>
          <b>{s.name}</b> <small>{[s.folder?.split("/")[0], s.state].filter(Boolean).join(" · ")}</small>
        </button>)}
      </div>}
      <form className="rd-home-compose" onSubmit={event => { event.preventDefault(); void ask(q); }}>
        <textarea ref={input} aria-label="Ask DuckTerm" rows={1} value={q}
          aria-keyshortcuts="/"
          placeholder={messaging ? "Ask about your teams, or @ a session to ask it…" : "Ask about your teams, the factory, PRs or spend…"}
          onChange={event => setQ(event.target.value)}
          onKeyDown={event => {
            if ((event.key === "Tab" || event.key === "Enter") && matches.length) { event.preventDefault(); pick(matches[0]); return; }
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void ask(q); }
          }} />
        <kbd className="rd-home-kbd" title="Press / to ask from anywhere on Home">/</kbd>
        <button className="rd-btn rd-btn-primary rd-btn-sm" disabled={pending !== null}>Ask</button>
      </form>
    </div>
  </div>;
}

// What came back from a session the owner asked with @. The answer is the
// agent's own text, so it shows as plain text, not rendered markup.
function Reply({ exchange }: { exchange: OracleExchange }) {
  const { to, reply } = exchange;
  if (reply?.status === "answered") return <div className="rd-home-reply"><small><b>{to}</b> answered</small><p>{reply.answer}</p></div>;
  if (reply && SETTLED.has(reply.status)) return <p className="rd-home-reply-wait">{to}'s message was {reply.status}; it will not answer.</p>;
  return <p className="rd-home-reply-wait" role="status">Waiting for {to} to answer{reply?.status === "accepted" ? " (it has started)" : ""}…</p>;
}
