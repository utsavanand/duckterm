import { useEffect, useRef, useState } from "react";
import { api, BroadcastResult, BroadcastTarget } from "./api";
import { Button, inputStyle, Modal } from "./ui";
import "./inbox.css";

export function MessageFolderModal({ folder, onClose }: { folder: string; onClose: () => void }) {
  const [targets, setTargets] = useState<BroadcastTarget[] | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<BroadcastResult | null>(null);
  const submission = useRef<{ text: string; key: string }>();
  const sending = useRef(false);
  const count = targets?.filter((target) => target.eligible).length ?? 0;
  const tooLong = new TextEncoder().encode(text).length > 16384;
  useEffect(() => {
    let cancelled = false;
    void api.broadcastTargets(folder).then(({ targets }) => {
      if (!cancelled) { setTargets(targets); setError(""); }
    }).catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [folder, retry]);

  async function send() {
    if (sending.current || !text.trim() || !count || tooLong) return;
    sending.current = true;
    setBusy(true);
    setError("");
    if (!submission.current || submission.current.text !== text) {
      submission.current = { text, key: crypto.randomUUID() };
    }
    try {
      setResult(await api.broadcast(folder, text, submission.current.key));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return <Modal title={`Message ${folder}`} onClose={() => { if (!busy) onClose(); }}>
    <div className="rd-message-folder" role="region" aria-label="Message folder">
      {result ? <>
        <p role="status">Message queued for {result.queued} {result.queued === 1 ? "session" : "sessions"}.{result.skipped > 0 && ` ${result.skipped} skipped.`}</p>
        <p>Sessions can read it in their inbox at their next pause. No reply is required.</p>
        <ul>{result.results.map((target) => <li key={target.session_id}>{target.name}: {target.status}{target.status === "skipped" && target.reason ? ` · ${target.reason}` : ""}</li>)}</ul>
        <Button onClick={onClose}>Done</Button>
      </> : <>
        <p>Send an inbox message to sessions in this folder and its subfolders.</p>
        {targets === null && !error && <p role="status">Loading recipients…</p>}
        {targets !== null && <details open>
          <summary>{count} {count === 1 ? "recipient" : "recipients"}</summary>
          <ul>{targets.map((target) => <li key={target.session_id}><strong>{target.name}</strong> · {target.eligible ? target.state : `Skipped: ${target.reason}`}</li>)}</ul>
          {count === 0 && <p>No eligible sessions in this folder.</p>}
        </details>}
        <label htmlFor="folder-message">Message</label>
        <textarea id="folder-message" style={inputStyle} rows={5} value={text} disabled={busy} onChange={(event) => setText(event.target.value)} placeholder="What should these sessions know?" />
        <p>Each session receives a message from <strong>You · Owner</strong>. Sessions can read it at their next pause. No reply is required.</p>
        {tooLong && <p role="alert">Message must be 16 KB or less.</p>}
        {error && <p role="alert">{error}{targets === null && <Button variant="ghost" onClick={() => setRetry((n) => n + 1)}>Retry recipients</Button>}</p>}
        <footer><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button disabled={busy || !count || !text.trim() || tooLong} onClick={() => void send()}>{busy ? "Sending…" : `Send to ${count} ${count === 1 ? "session" : "sessions"}`}</Button></footer>
      </>}
    </div>
  </Modal>;
}
