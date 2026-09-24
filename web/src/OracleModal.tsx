import { useState } from "react";
import { api } from "./api";
import { Button, inputStyle, Modal } from "./ui";

export type OracleExchange = { q: string; a: string };

// Answers come from /fleet/ask: one summarizer call over a digest of every
// running session. The server is stateless, so the log lives in the caller
// (it survives closing the modal) and the last two exchanges ride along for
// follow-up questions.
export function OracleModal({
  log,
  onLog,
  onClose,
}: {
  log: OracleExchange[];
  onLog: (next: OracleExchange) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask() {
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    try {
      const r = await api.fleetAsk(question, log.slice(-2));
      onLog({ q: question, a: r.answer });
      setQ("");
    } catch (e) {
      onLog({ q: question, a: `Failed: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Ask Oracle" onClose={onClose}>
      <div className="rd-oracle">
        {log.length === 0 && (
          <p className="rd-modal-hint">
            Oracle reads every running session's state, goal, and recent output.
            Try “who's stuck?” or “what has entourage done so far?”
          </p>
        )}
        {log.length > 0 && (
          <div className="rd-oracle-log" aria-label="Oracle answers">
            {log.map((x, i) => (
              <div key={i} className="rd-oracle-exchange">
                <div className="rd-oracle-q">❯ {x.q}</div>
                <div className="rd-oracle-a">{x.a}</div>
              </div>
            ))}
          </div>
        )}
        <label htmlFor="oracle-question" className="rd-oracle-label">
          Question
        </label>
        <input
          id="oracle-question"
          autoFocus
          style={inputStyle}
          value={q}
          disabled={busy}
          placeholder="Ask about your running sessions"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void ask();
            if (e.key === "Escape") onClose();
          }}
        />
        <footer className="rd-oracle-footer">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button disabled={busy || !q.trim()} onClick={() => void ask()}>
            {busy ? "Asking…" : "Ask"}
          </Button>
        </footer>
      </div>
    </Modal>
  );
}
