import { useEffect, useRef, useState } from "react";
import { api, authHeaders } from "./api";
import { html } from "./render";
import { useToast } from "./ui";

// Structured view of an agent's latest reply (HTML-annotation mode,
// docs/structured-render-design.md). Renders the response as HTML; select any
// span to attach a note, which is stored AND sent back to the agent as a
// follow-up prompt.

type Block =
  | { type: "text"; text: string }
  | { type: "tool_use"; name: string; input?: unknown }
  | { type: "tool_result"; text: string };

interface Message {
  id: number;
  role: "user" | "assistant";
  blocks: Block[];
}

interface Selection {
  quote: string;
  x: number;
  y: number;
}

export function Messages({ sessionKey }: { sessionKey: string }) {
  const toast = useToast();
  const [messages, setMessages] = useState<Message[]>([]);
  const [loaded, setLoaded] = useState(false);
  // How many turns BACK from the newest we're viewing (0 = latest).
  const [back, setBack] = useState(0);
  useEffect(() => {
    setBack(0); // a different session starts at its latest turn
  }, [sessionKey]);
  const [sel, setSel] = useState<Selection | null>(null);
  const [note, setNote] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [sending, setSending] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Steer the agent without switching to the terminal: the same stdin path
  // the terminal types into, one text box away from the rendered reply.
  async function sendFollowUp() {
    const text = followUp.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await api.sendInput(sessionKey, text + "\n");
      setFollowUp("");
      toast("Sent to the agent");
    } catch (e) {
      toast(`Send failed: ${(e as Error).message}`, "err");
    } finally {
      setSending(false);
    }
  }

  // Capture a text selection inside the messages and anchor a note popover to it.
  const onMouseUp = () => {
    const s = window.getSelection();
    const text = s?.toString().trim();
    if (!text || !s || s.rangeCount === 0) {
      if (!note) setSel(null);
      return;
    }
    const rect = s.getRangeAt(0).getBoundingClientRect();
    const wrap = wrapRef.current?.getBoundingClientRect();
    setSel({
      quote: text,
      x: rect.left - (wrap?.left ?? 0),
      y: rect.bottom - (wrap?.top ?? 0) + (wrapRef.current?.scrollTop ?? 0),
    });
  };

  async function submitAnnotation() {
    if (!sel || !note.trim()) return;
    try {
      const res = await fetch(`/sessions/${sessionKey}/annotations`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ quote: sel.quote, note: note.trim() }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error ?? "failed");
      toast(d.sent ? "Sent to the agent" : "Saved (agent not live)");
    } catch (e) {
      toast(`Annotation failed: ${(e as Error).message}`, "err");
    } finally {
      setSel(null);
      setNote("");
      window.getSelection()?.removeAllRanges();
    }
  }

  useEffect(() => {
    let live = true;
    const load = () =>
      fetch(`/sessions/${sessionKey}/messages`)
        .then((r) => r.json())
        .then((d: { messages?: Message[] }) => {
          if (live) {
            setMessages(d.messages ?? []);
            setLoaded(true);
          }
        })
        .catch(() => undefined);
    load();
    // The transcript grows as the agent works; refresh on a light interval.
    const t = setInterval(load, 3000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [sessionKey]);

  // One interaction turn at a time, defaulting to the newest. `back` counts
  // turns from the end, so while you're on the latest (back=0) new turns keep
  // appearing in place; while browsing older ones your position holds steady.
  const turns = turnsOf(messages);
  const latest = turns.length ? turns[Math.max(0, turns.length - 1 - back)] : null;

  if (loaded && !latest) {
    return (
      <div className="rd-panel-empty">
        No agent reply yet (claude-code and codex sessions only).
      </div>
    );
  }
  if (!latest) return <div className="rd-messages" />;
  const showingLatest = back === 0;

  return (
    <div className="rd-messages" ref={wrapRef} onMouseUp={onMouseUp}>
      {/* Step through interaction turns; ‹ goes to the previous exchange. */}
      {turns.length > 1 && (
        <div className="rd-turn-nav">
          <button
            aria-label="Previous turn"
            disabled={back >= turns.length - 1}
            onClick={() => setBack((b) => Math.min(b + 1, turns.length - 1))}
          >
            ‹
          </button>
          <span>
            turn {turns.length - back} / {turns.length}
          </span>
          <button
            aria-label="Next turn"
            disabled={back === 0}
            onClick={() => setBack((b) => Math.max(b - 1, 0))}
          >
            ›
          </button>
        </div>
      )}
      {/* One exchange, typeset like the tool it lives in: the prompt
          as a shell line, the reply as plain prose. No chat bubbles. */}
      {latest.prompt && (
        <div className="rd-turn-user">
          <span className="rd-prompt-mark">❯</span>
          <span className="rd-turn-prompt">{latest.prompt}</span>
        </div>
      )}
      {latest.tools.length > 0 && (
        <div className="rd-msg-tools">
          {toolCounts(latest.tools).map(([name, n]) => (
            <span key={name} className="rd-tool-chip">
              {name}
              {n > 1 ? ` ×${n}` : ""}
            </span>
          ))}
        </div>
      )}
      {latest.texts.length === 0 && (latest.prompt || latest.tools.length) ? (
        <div className="rd-msg-pending">
          {showingLatest ? "working — no reply yet" : "no reply in this turn"}
        </div>
      ) : (
        latest.texts.map((t, i) => (
          <div
            key={i}
            className="rd-msg-text"
            dangerouslySetInnerHTML={{ __html: html(t) }}
          />
        ))
      )}
      <div className="rd-followup">
        <span className="rd-prompt-mark">❯</span>
        <input
          value={followUp}
          placeholder="send a follow-up to the agent…"
          onChange={(e) => setFollowUp(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") sendFollowUp();
          }}
        />
        <button
          className="rd-btn rd-btn-sm rd-btn-primary"
          onClick={sendFollowUp}
          disabled={sending || !followUp.trim()}
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </div>
      {sel && (
        <div
          className="rd-annotate-pop"
          style={{ left: sel.x, top: sel.y + 6 }}
          onMouseUp={(e) => e.stopPropagation()}
        >
          <div className="rd-annotate-quote">“{sel.quote.slice(0, 80)}”</div>
          <textarea
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="note to send back to the agent…"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey))
                submitAnnotation();
              if (e.key === "Escape") {
                setSel(null);
                setNote("");
              }
            }}
          />
          <div className="rd-annotate-actions">
            <button onClick={submitAnnotation} disabled={!note.trim()}>
              Send ⌘↵
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

interface Reply {
  prompt: string | null; // the user prompt that started this turn
  texts: string[]; // assistant prose blocks in the turn
  tools: string[]; // tool names the agent ran in the turn
}

// Split the transcript into interaction turns: each user text message starts a
// new turn; everything until the next one (assistant prose, tool calls) is that
// turn's reply. Leading assistant-only content forms its own turn.
function turnsOf(messages: Message[]): Reply[] {
  const turns: Reply[] = [];
  let cur: Reply | null = null;
  for (const m of messages) {
    const isPrompt =
      m.role === "user" && m.blocks.some((b) => b.type === "text");
    if (isPrompt || cur === null) {
      cur = { prompt: null, texts: [], tools: [] };
      turns.push(cur);
    }
    for (const b of m.blocks) {
      if (b.type === "text") {
        if (m.role === "user" && cur.prompt === null) cur.prompt = b.text;
        else if (m.role === "assistant") cur.texts.push(b.text);
      } else if (b.type === "tool_use") {
        cur.tools.push(b.name);
      }
    }
  }
  return turns.filter((t) => t.prompt || t.texts.length || t.tools.length);
}

// Tool usage as (name, count) pairs, most-used first — rendered as chips.
function toolCounts(tools: string[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const t of tools) counts.set(t, (counts.get(t) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}
