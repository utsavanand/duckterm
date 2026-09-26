import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, Artifact } from "./api";
import "./artifactFeedback.css";

export interface ArtifactSelection { quote: string; left: number; bottom: number }
export interface FeedbackTarget extends ArtifactSelection { artifact: Artifact }

export function ArtifactFeedback({ sessionKey, sessionName, target, onClose, onSent }: {
  sessionKey: string; sessionName: string; target: FeedbackTarget; onClose: () => void; onSent: () => void;
}) {
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const pop = useRef<HTMLElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const [position, setPosition] = useState({ left: 12, top: 90 });
  useEffect(() => { input.current?.focus(); }, []);
  useLayoutEffect(() => {
    const place = () => {
      const box = pop.current?.getBoundingClientRect();
      setPosition({ left: Math.max(12, Math.min(target.left, innerWidth - (box?.width ?? 340) - 12)), top: Math.max(12, Math.min(target.bottom + 8, innerHeight - (box?.height ?? 280) - 12)) });
    };
    place(); window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [target, error]);
  async function send() {
    if (sending || !note.trim()) return;
    setSending(true); setError("");
    try {
      await api.artifactFeedback(sessionKey, target.artifact, target.quote, note.trim());
      onSent();
    } catch (cause) { setError((cause as Error).message); }
    finally { setSending(false); }
  }
  return <section ref={pop} className="rd-artifact-feedback" role="dialog" aria-label="Artifact feedback" style={position}>
    <div className="rd-feedback-head">Artifact feedback<button type="button" aria-label="Close feedback" disabled={sending} onClick={onClose}>×</button></div>
    <p className="rd-feedback-to">To {sessionName} · {target.artifact.source_path.split("/").pop()}</p>
    <blockquote>{target.quote ? `“${target.quote}”` : "Feedback on the whole artifact"}</blockquote>
    <textarea ref={input} aria-label="Feedback to agent" placeholder="What would you like changed?" value={note} maxLength={8000} disabled={sending} onChange={e => setNote(e.target.value)} onKeyDown={e => {
      if (e.key === "Escape" && !sending) { e.preventDefault(); onClose(); }
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(); }
    }} />
    {error && <p role="alert" className="rd-feedback-error">{error}</p>}
    <div className="rd-feedback-actions"><button disabled={sending} onClick={onClose}>Cancel</button><button className="rd-feedback-send" disabled={sending || !note.trim()} onClick={() => void send()}>{sending ? "Sending…" : "Send ⌘↵"}</button></div>
  </section>;
}
