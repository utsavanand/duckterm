import { useEffect, useRef, useState } from "react";
import { api, OracleExchange } from "./api";
import { FolderRecipient, FolderRecipientPicker } from "./FolderRecipientPicker";
import { Modal } from "./ui";
import { ArtifactsView } from "./ArtifactsView";
import { desktop } from "./desktop";
import { html } from "./render";
import "./folderView.css";

// Folder panels are ordinary internal components; sessions remain in the tree.
export function FolderView({ folder }: { folder: string }) {
  const [tab, setTab] = useState<"chat" | "artifacts">("chat");
  const tabs = useRef<HTMLDivElement>(null);
  return <section className="rd-folder-view" aria-label={`Folder ${folder}`}>
    <header className="rd-folder-heading"><small>Folder</small><h1>{folder.split("/").pop()}</h1><p>{folder} · Includes subfolders{desktop() ? " · This Mac" : ""}</p></header>
    <div ref={tabs} className="rd-folder-tabs" role="tablist" aria-label="Folder views" onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? "chat" : event.key === "End" ? "artifacts" : tab === "chat" ? "artifacts" : "chat";
      setTab(next);
      tabs.current?.querySelector<HTMLButtonElement>(`#folder-${next}-tab`)?.focus();
    }}>
      {(["chat", "artifacts"] as const).map(name => <button key={name} id={`folder-${name}-tab`} role="tab" aria-controls={`folder-${name}`} aria-selected={tab === name} tabIndex={tab === name ? 0 : -1} onClick={() => setTab(name)}>{name === "chat" ? "Chat" : "Artifacts"}</button>)}
    </div>
    <div id="folder-chat" role="tabpanel" aria-labelledby="folder-chat-tab" className="rd-folder-chat-panel" hidden={tab !== "chat"}><FolderChat key={folder} folder={folder} active={tab === "chat"} /></div>
    <div id="folder-artifacts" role="tabpanel" aria-labelledby="folder-artifacts-tab" className="rd-folder-artifacts-panel" hidden={tab !== "artifacts"}>{tab === "artifacts" && <ArtifactsView key={folder} folder={folder} />}</div>
  </section>;
}

function FolderChat({ folder, active }: { folder: string; active: boolean }) {
  const [messages, setMessages] = useState<OracleExchange[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [retry, setRetry] = useState(0);
  const [recipient, setRecipient] = useState<FolderRecipient | null>(null);
  const [picker, setPicker] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const submission = useRef<{ signature: string; key: string }>();
  const log = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);
  const sending = useRef(false);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    setError("");
    void api.folderChat(folder).then(result => {
      if (!cancelled) { setMessages(result.messages); setLoaded(true); }
    }).catch((cause: Error) => { if (!cancelled) setError(`Could not load this folder’s conversation: ${cause.message}`); });
    return () => { cancelled = true; };
  }, [folder, retry]);
  useEffect(() => {
    if (!active || !loaded || !messages.some(m => m.dispatch?.recipients.some(r => ["queued", "accepted", "read"].includes(r.status)))) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await api.folderChat(folder);
        if (!cancelled && !sending.current) setMessages(previous => JSON.stringify(previous) === JSON.stringify(result.messages) ? previous : result.messages);
      } catch { /* Keep the last delivered messages; send errors remain visible. */ }
      finally { if (!cancelled) timer = setTimeout(() => void refresh(), 3000); }
    }
    timer = setTimeout(() => void refresh(), 3000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active, folder, loaded, messages]);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages, pending]);
  function choose(target: FolderRecipient | null) {
    setRecipient(target);
    setPicker(null);
    if (target) setQuestion(value => value.replace(/@[^@\n]*$/, ""));
    input.current?.focus();
  }
  function send() {
    if (!question.trim() || sending.current) return;
    setPicker(null);
    if (recipient?.kind === "folder") setReviewing(true);
    else void ask();
  }
  async function ask(text = question, answerOnly = false) {
    const value = text.trim();
    if (!loaded || sending.current || !value) return;
    sending.current = true;
    setPending(value);
    setError("");
    setQuestion("");
    const target = answerOnly ? null : recipient;
    try {
      const signature = JSON.stringify([target, value]);
      if (!submission.current || submission.current.signature !== signature) submission.current = { signature, key: crypto.randomUUID() };
      const result = target ? await api.folderDispatch(folder, {
        identity: target.identity, target: { kind: target.kind, id: target.id }, text: value,
        request_key: submission.current.key, recipients: target.recipients.filter(r => r.eligible).map(r => r.session_id),
      }) : await api.fleetAsk(value, folder);
      if (mounted.current) setMessages(previous => result.exchange.dispatch && previous.some(m => m.dispatch?.request_key === result.exchange.dispatch?.request_key) ? previous : [...previous, result.exchange]);
    } catch (cause) {
      if (mounted.current) { setError((cause as Error).message); setQuestion(current => current || value); }
    } finally {
      sending.current = false;
      if (mounted.current) { setPending(null); input.current?.focus(); }
    }
  }
  return <div className="rd-folder-chat">
    <div className="rd-folder-chat-heading"><h2>Folder chat</h2><span>Ask or message a session</span></div>
    <p className="rd-folder-intro">Answers from {folder} and its subfolders{desktop() ? " on this Mac" : ""}.</p>
    <div ref={log} className="rd-folder-conversation" aria-live="polite" aria-label="Folder conversation">
      {!loaded && !error && <p role="status">Loading conversation…</p>}
      {loaded && messages.length === 0 && !pending && <p className="rd-folder-intro">Ask what needs your attention or what the sessions in this folder are working on.</p>}
      {messages.map((message, index) => <div className="rd-folder-exchange" key={`${message.at}-${index}`}><div className="rd-folder-question"><small>You{message.dispatch ? ` → ${message.dispatch.label}` : ""}</small><p>{message.q}</p></div>{message.dispatch ? <div className="rd-folder-delivery">{message.dispatch.recipients.map(r => <div key={r.message_id}><small>{r.name} · {({ queued: "Queued in inbox", accepted: "Accepted", read: "Read", answered: "Answered", declined: "Declined", expired: "Expired", cancelled: "Cancelled" })[r.status]}</small>{r.answer && <div className="rd-folder-answer"><small>{r.name} · Reply</small><div dangerouslySetInnerHTML={{ __html: html(r.answer) }} /></div>}</div>)}</div> : <div className="rd-folder-answer"><small>Folder answer</small><div dangerouslySetInnerHTML={{ __html: html(message.a) }} /></div>}</div>)}
      {pending !== null && <div className="rd-folder-exchange"><div className="rd-folder-question"><small>You</small><p>{pending}</p></div><p role="status">{recipient ? "Queuing your message…" : "Reading this folder’s sessions…"}</p></div>}
    </div>
    {error && <div className="rd-folder-error" role="alert">{error}{!loaded && <button className="rd-btn rd-btn-sm" onClick={() => setRetry(n => n + 1)}>Retry</button>}</div>}
    <div className="rd-folder-suggestions">{["What needs my attention?", "What is this folder working on?"].map(text => <button key={text} disabled={!loaded || pending !== null} onClick={() => void ask(text, true)}>{text}</button>)}</div>
    <form className="rd-folder-compose" onSubmit={event => { event.preventDefault(); send(); }}>
      {recipient && <div className="rd-folder-target"><span>@{recipient.name} · {recipient.kind === "folder" ? "Subfolder" : "Session"}</span><button type="button" aria-label="Remove recipient" disabled={pending !== null} onClick={() => choose(null)}>×</button></div>}
      {picker !== null && <FolderRecipientPicker folder={folder} query={picker} onPick={choose} onClose={() => { setPicker(null); input.current?.focus(); }} />}
      <textarea ref={input} disabled={pending !== null} aria-label="Ask this folder" placeholder="Ask about this folder, or type @ to message a session…" rows={2} maxLength={8000} value={question} onChange={event => { setQuestion(event.target.value); const match = event.target.value.match(/@([^@\n]*)$/); setPicker(match ? match[1] : null); }} onKeyDown={event => {
        if (event.key === "ArrowDown" && picker !== null) { event.preventDefault(); document.querySelector<HTMLButtonElement>('#folder-recipient-picker button[role="option"]:not(:disabled)')?.focus(); return; }
        if (event.key === "Escape") { setPicker(null); return; }
        if (picker !== null && event.key === "Enter") { event.preventDefault(); return; }
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
      }} />
      <div><span><button type="button" className="rd-folder-mention" aria-label="Choose recipient" disabled={pending !== null} onClick={() => setPicker(picker === null ? "" : null)}> @ </button> {recipient ? recipient.kind === "folder" ? "Broadcast to subfolder" : "Direct message" : "Folder answer"}</span><button className="rd-btn rd-btn-primary rd-btn-sm" disabled={!loaded || pending !== null || !question.trim()}>{recipient ? recipient.kind === "folder" ? "Review recipients" : `Send to ${recipient.name}` : "Ask"}</button></div>
    </form>
    <p className="rd-folder-helper">{recipient ? recipient.kind === "folder" ? "Review who receives your message before sending. Replies are optional." : "Sent as your message. The session’s reply appears here." : "Without a recipient, the folder answers from its sessions. No work is sent."}</p>
    {reviewing && recipient && <Modal title="Review recipients" onClose={() => setReviewing(false)}>
      <section role="dialog" aria-label="Review recipients">
      <p>Message {recipient.name} and its subfolders.</p>
      <ul>{recipient.recipients.map(r => <li key={r.session_id}>{r.name} · {r.eligible ? r.state : `Skipped: ${r.reason}`}</li>)}</ul>
      <p className="rd-folder-review-text">{question}</p>
      <p>Each recipient receives your inbox message. Replies are optional.</p>
      <button className="rd-btn rd-btn-ghost" onClick={() => setReviewing(false)}>Cancel</button>
      <button className="rd-btn rd-btn-primary" onClick={() => { setReviewing(false); void ask(); }}>Send to {recipient.recipients.filter(r => r.eligible).length} sessions</button>
      </section>
    </Modal>}
  </div>;
}
