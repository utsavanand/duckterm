import { useState } from "react";
import { api } from "./api";

// Ask questions about every running session from one bar. Answers come from
// the server's /fleet/ask (a digest of all running sessions through the
// configured summarizer backend). The exchange log lives here, client-side —
// the server is stateless; the last two Q/As ride along for follow-ups.
export function FleetChat() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [log, setLog] = useState<{ q: string; a: string }[]>([]);

  async function ask() {
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    setOpen(true);
    try {
      const r = await api.fleetAsk(question, log.slice(-2));
      setLog((l) => [...l, { q: question, a: r.answer }]);
      setQ("");
    } catch (e) {
      setLog((l) => [
        ...l,
        { q: question, a: `Failed: ${(e as Error).message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rd-fleetchat">
      <div className="rd-fleetchat-bar">
        <span className="rd-fleetchat-prompt">?</span>
        <input
          value={q}
          placeholder="Ask about your running sessions — “who's stuck?”, “what has entourage done so far?”"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") ask();
            if (e.key === "Escape") setOpen(false);
          }}
        />
        {busy && <span className="rd-spinner" />}
        {log.length > 0 && (
          <button
            className="rd-btn rd-btn-sm rd-btn-ghost"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "Hide" : `Answers (${log.length})`}
          </button>
        )}
      </div>
      {open && log.length > 0 && (
        <div className="rd-fleetchat-log">
          {log.map((x, i) => (
            <div key={i} className="rd-fleetchat-exchange">
              <div className="rd-fleetchat-q">❯ {x.q}</div>
              <div className="rd-fleetchat-a">{x.a}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
