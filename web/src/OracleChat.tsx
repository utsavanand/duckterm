import { useEffect, useRef, useState } from "react";
import { api, OracleAgent, OracleExchange, RelayNote, RelayRule, RelayState } from "./api";
import { useEdition } from "./edition";
import { modelHtml } from "./render";
import { NoteCard, ProposalCard, RuleFields } from "./RelayNotes";

// Chat-only entries: rule proposals and short replies to rule commands.
type Local =
  | { at: number; kind: "proposal"; rule: RelayRule; state: "open" | "created" | "cancelled"; id?: string }
  | { at: number; kind: "info"; text: string; rules?: RelayRule[] }
  // A message Oracle will send only after the owner confirms it.
  | { at: number; kind: "send"; agent: OracleAgent; text: string; question: string; key: string; state: "open" | "sending" | "logged" | "cancelled"; error?: string }
  | { at: number; kind: "clarify"; question: string; text: string; candidates: OracleAgent[]; state: "open" | "done" };

const requestKey = () => crypto.randomUUID().replace(/-/g, "");

type Entry =
  | { at: number; type: "exchange"; exchange: OracleExchange }
  | { at: number; type: "note"; note: RelayNote }
  | { at: number; type: "local"; local: Local; index: number };

// "always …", "never …", "when an agent asks …": a rule, not a question.
const RULE_WORDS = /^(always|never|when|whenever|if an agent|if agents)\b/i;

// Only questions the digest can answer: it carries each session's state,
// goal, context size, last checkpoint, and recent screen, but no inbox data.
const SUGGESTIONS = [
  "Which sessions are waiting on me?",
  "What is each session working on right now?",
  "Which sessions are running low on context?",
  "Are any two sessions working on the same thing?",
];

// A docked chat with Oracle: answers come from /fleet/ask (one summarizer
// call over a digest of every running session). The server stores the
// conversation, so it survives reloads and is shared by the browser and the
// Mac app; it also supplies the last exchanges as follow-up context.
export function OracleChat({
  relay,
  onRelayChange,
  onClose,
  onOpenTerminal,
  agents = [],
}: {
  relay: RelayState;
  onRelayChange: () => void;
  onClose?: () => void;
  onOpenTerminal?: (key: string) => void;
  agents?: OracleAgent[];
}) {
  const messaging = useEdition().has("oracle_messages");
  const [recipient, setRecipient] = useState<OracleAgent | null>(null);
  const [picking, setPicking] = useState(false);
  const [filter, setFilter] = useState("");
  const [locals, setLocals] = useState<Local[]>([]);
  const [log, setLog] = useState<OracleExchange[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [q, setQ] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  // Grow with the text up to the CSS max-height, then scroll.
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [q]);

  useEffect(() => {
    let stale = false;
    api
      .oracleChat()
      .then((r) => { if (!stale) setLog(r.messages); })
      .catch((e: Error) => { if (!stale) setError(`Could not load the conversation: ${e.message}`); })
      .finally(() => { if (!stale) setLoaded(true); });
    return () => { stale = true; };
  }, []);

  useEffect(() => {
    // Scroll only the message list. scrollIntoView also scrolled every
    // scrollable ancestor, which pushed the control tower's header off screen.
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [log, pending, error, locals.length, relay.notes.length]);

  const localCount = useRef(0);
  const addLocal = (l: Local) => {
    setLocals((ls) => [...ls, l]);
    return localCount.current++;
  };

  async function propose(text: string) {
    setPending(text);
    setError("");
    try {
      const { rule } = await api.proposeRule(text);
      addLocal({ at: Date.now(), kind: "proposal", rule, state: "open" });
    } catch (e) {
      addLocal({ at: Date.now(), kind: "info", text: (e as Error).message });
    } finally {
      setPending(null);
    }
  }

  async function send(text: string = q) {
    const t = text.trim();
    if (!t || pending !== null) return;
    setQ("");
    // Text to a chosen agent is theirs as typed, even "rules" or "stop R1".
    if (recipient) {
      const key = requestKey(), question = `@${recipient.name} ${t}`;
      return deliver(addLocal({ at: Date.now(), kind: "send", agent: recipient, text: t, question, key, state: "open" }), recipient, t, key, question);
    }
    const stop = t.match(/^stop\s+(r\d+)$/i);
    if (stop) {
      try {
        await api.deleteRule(stop[1].toUpperCase());
        addLocal({ at: Date.now(), kind: "info", text: `Stopped ${stop[1].toUpperCase()}.` });
      } catch (e) {
        addLocal({ at: Date.now(), kind: "info", text: (e as Error).message });
      }
      onRelayChange();
      return;
    }
    if (/^rules$/i.test(t)) {
      addLocal({ at: Date.now(), kind: "info", text: relay.rules.length ? "Your rules:" : "No rules yet. Start one with \"always …\" or \"when an agent asks …\".", rules: relay.rules });
      return;
    }
    if (RULE_WORDS.test(t)) return propose(t);
    return ask(t);
  }

  // The owner's click, or their own words to one chosen agent, is the send.
  async function deliver(index: number, agent: OracleAgent, text: string, key: string, question: string) {
    setLocal(index, { state: "sending", error: undefined });
    try {
      const { exchange } = await api.messageSession(agent.id, text, "inbox", key, question);
      // The saved exchange replaces the card, so the send is in the history.
      if (exchange) setLog((l) => [...l, exchange]);
      setLocal(index, { state: "logged" });
    } catch (e) {
      setLocal(index, { state: "open", error: (e as Error).message });
    }
  }

  // Picking the agent isn't approving the text: the model chose the words, so
  // they wait on a Send card like any other plan.
  function pick(index: number, local: Extract<Local, { kind: "clarify" }>, agent: OracleAgent) {
    setLocal(index, { state: "done" });
    addLocal({ at: Date.now(), kind: "send", agent, text: local.text, question: local.question, key: requestKey(), state: "open" });
  }

  const entries: Entry[] = [
    ...log.map((exchange) => ({ at: exchange.at, type: "exchange" as const, exchange })),
    ...relay.notes.map((note) => ({ at: note.created_at, type: "note" as const, note })),
    ...locals.map((local, index) => ({ at: local.at, type: "local" as const, local, index })),
  ].sort((a, b) => a.at - b.at);

  function setLocal(index: number, patch: Partial<Local>) {
    setLocals((ls) => ls.map((l, i) => (i === index ? ({ ...l, ...patch } as Local) : l)));
  }

  async function ask(text: string = q) {
    const question = text.trim();
    if (!question || pending !== null) return;
    setPending(question);
    setQ("");
    setError("");
    try {
      if (!messaging) {
        const r = await api.fleetAsk(question);
        setLog((l) => [...l, r.exchange]);
        return;
      }
      const r = await api.fleetAsk(question, undefined, requestKey());
      if ("plan" in r) {
        const { text, ...agent } = r.plan;
        addLocal({ at: Date.now(), kind: "send", agent, text, question, key: requestKey(), state: "open" });
      } else if ("clarify" in r) {
        addLocal({ at: Date.now(), kind: "clarify", question, ...r.clarify, state: "open" });
      } else {
        setLog((l) => [...l, r.exchange]);
      }
    } catch (e) {
      setError(`Oracle could not answer: ${(e as Error).message}`);
      setQ(question);
    } finally {
      setPending(null);
    }
  }

  async function clear() {
    try {
      setLog((await api.clearOracleChat()).messages);
    } catch (e) {
      setError(`Could not clear: ${(e as Error).message}`);
    }
  }

  return (
    <aside className="rd-oracle" aria-label="Oracle chat">
      <header className="rd-oracle-head">
        <span className="rd-oracle-title">Ask Oracle</span>
        {relay.open > 0 && <span className="rd-relay-count">{relay.open} need{relay.open === 1 ? "s" : ""} you</span>}
        <span className="rd-spacer" />
        {log.length > 0 && (
          <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => void clear()}>
            Clear
          </button>
        )}
        {onClose && (
          <button
            className="rd-btn rd-btn-ghost rd-btn-sm"
            onClick={onClose}
            aria-label="Close Oracle"
          >
            ✕
          </button>
        )}
      </header>
      <div className="rd-oracle-log" ref={logRef}>
        {loaded && entries.length === 0 && pending === null && (
          <div className="rd-oracle-empty">
            <p>
              Ask about all your running sessions at once. Oracle reads each
              session's state, goal, context size, and recent terminal output.
              When an agent needs you, its question shows up here to answer.
            </p>
            <div className="rd-oracle-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="rd-oracle-suggestion" onClick={() => void send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {entries.map((e) =>
          e.type === "exchange" ? (
            <div key={`x-${e.at}`} className="rd-oracle-exchange">
              <div className="rd-oracle-q">{e.exchange.q}</div>
              <div
                className="rd-oracle-a rd-msg-text"
                dangerouslySetInnerHTML={{ __html: modelHtml(e.exchange.a) }}
              />
            </div>
          ) : e.type === "note" ? (
            <NoteCard key={e.note.id} note={e.note} rules={relay.rules} onChange={onRelayChange} onPropose={(t) => void propose(t)} onOpenTerminal={onOpenTerminal} />
          ) : e.local.kind === "proposal" ? (
            e.local.state === "open" ? (
              <ProposalCard
                key={`l-${e.index}`}
                rule={e.local.rule}
                onCreated={(rule) => { setLocal(e.index, { state: "created", id: rule.id }); onRelayChange(); }}
                onCancel={() => setLocal(e.index, { state: "cancelled" })}
              />
            ) : (
              <p key={`l-${e.index}`} className="rd-relay-status">
                {e.local.state === "created"
                  ? `Created ${e.local.id}: ${e.local.rule.summary || "rule"}. Say "stop ${e.local.id}" to turn it off.`
                  : "Rule not created."}
              </p>
            )
          ) : e.local.kind === "send" ? (
            <SendCard
              key={`l-${e.index}`}
              local={e.local}
              onSend={(l) => void deliver(e.index, l.agent, l.text, l.key, l.question)}
              onCancel={() => setLocal(e.index, { state: "cancelled" })}
            />
          ) : e.local.kind === "clarify" ? (
            <div key={`l-${e.index}`} className="rd-relay-info rd-oracle-clarify" role="group" aria-label="Which agent did you mean?">
              <p>{e.local.candidates.length ? "Which agent did you mean? Nothing was sent." : "I couldn't tell which agent you mean. Nothing was sent. Pick one with @."}</p>
              {e.local.state === "open" && e.local.candidates.length > 0 && (
                <div className="rd-relay-actions">
                  {e.local.candidates.map((a) => (
                    <button key={a.id} className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => pick(e.index, e.local as Extract<Local, { kind: "clarify" }>, a)}>
                      {a.name}{a.folder ? <small> · {a.folder.split("/").pop()}</small> : null}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div key={`l-${e.index}`} className="rd-relay-info">
              <p>{e.local.text}</p>
              {e.local.rules?.map((r) => (
                <div key={r.id} className="rd-relay-rule">
                  <span className="rd-relay-kind rule">{r.id}</span>
                  <RuleFields rule={r} />
                </div>
              ))}
            </div>
          ),
        )}
        {pending !== null && (
          <div className="rd-oracle-exchange">
            <div className="rd-oracle-q">{pending}</div>
            <div className="rd-oracle-thinking" role="status">
              <span className="rd-spinner" /> Reading your sessions…
            </div>
          </div>
        )}
        {error && <p className="rd-oracle-error" role="alert">{error}</p>}
      </div>
      {picking && (
        <div className="rd-oracle-picker" role="listbox" aria-label="Choose an agent">
          <input autoFocus aria-label="Find an agent" placeholder="Find an agent" value={filter} onChange={(e) => setFilter(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setPicking(false); } }} />
          {agents.filter((a) => `${a.name} ${a.folder}`.toLowerCase().includes(filter.toLowerCase())).map((a) => (
            <button key={a.id} role="option" aria-selected={recipient?.id === a.id} onClick={() => { setRecipient(a); setPicking(false); setFilter(""); input.current?.focus(); }}>
              <b>{a.name}</b> <small>{a.folder}</small>
            </button>
          ))}
        </div>
      )}
      {recipient && (
        <div className="rd-oracle-to">
          To <b>{recipient.name}</b>, sent as you wrote it
          <button className="rd-btn rd-btn-ghost rd-btn-sm" aria-label={`Stop messaging ${recipient.name}`} onClick={() => setRecipient(null)}>✕</button>
        </div>
      )}
      <div className="rd-oracle-compose">
        {messaging && (
          <button className="rd-btn rd-btn-ghost rd-btn-sm rd-oracle-at" aria-label="Message one agent" aria-expanded={picking} onClick={() => setPicking((p) => !p)}>@</button>
        )}
        <textarea
          ref={input}
          aria-label="Message Oracle"
          value={q}
          rows={1}
          placeholder={recipient ? `Message ${recipient.name}` : messaging ? "Ask about your sessions, tell an agent something, or make a rule" : "Answer a note, ask about your sessions, or make a rule"}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <button
          className="rd-btn rd-btn-primary rd-btn-sm"
          disabled={pending !== null || !q.trim()}
          onClick={() => void send()}
        >
          Send
        </button>
      </div>
    </aside>
  );
}

function SendCard({ local, onSend, onCancel }: { local: Extract<Local, { kind: "send" }>; onSend: (l: Extract<Local, { kind: "send" }>) => void; onCancel: () => void }) {
  const { agent, text, state, error } = local;
  if (state === "logged") return null;
  return (
    <div className={`rd-relay-note rd-oracle-send${state === "open" ? " open" : ""}`} role="group" aria-label={`Message to ${agent.name}`}>
      <span className="rd-relay-meta">{state === "cancelled" ? "Not sent to" : "Send to"} <b>{agent.name}</b>{agent.folder ? ` · ${agent.folder}` : ""}</span>
      <blockquote className="rd-relay-quote">{text}</blockquote>
      {(state === "open" || state === "sending") && (
        <div className="rd-relay-actions">
          <button className="rd-btn rd-btn-primary rd-btn-sm" disabled={state === "sending"} onClick={() => onSend(local)}>Send</button>
          <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={state === "sending"} onClick={onCancel}>Cancel</button>
        </div>
      )}
      {error && <span className="rd-oracle-error" role="alert">{error}</span>}
    </div>
  );
}
