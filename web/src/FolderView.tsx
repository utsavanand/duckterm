import { useEffect, useRef, useState } from "react";
import { api, OracleExchange } from "./api";
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
    <div id="folder-chat" role="tabpanel" aria-labelledby="folder-chat-tab" className="rd-folder-chat-panel" hidden={tab !== "chat"}><FolderChat key={folder} folder={folder} /></div>
    <div id="folder-artifacts" role="tabpanel" aria-labelledby="folder-artifacts-tab" className="rd-folder-artifacts-panel" hidden={tab !== "artifacts"}>{tab === "artifacts" && <ArtifactsView key={folder} folder={folder} />}</div>
  </section>;
}

function FolderChat({ folder }: { folder: string }) {
  const [messages, setMessages] = useState<OracleExchange[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [retry, setRetry] = useState(0);
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
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages, pending]);
  async function ask(text = question) {
    const value = text.trim();
    if (!loaded || sending.current || !value) return;
    sending.current = true;
    setPending(value);
    setError("");
    setQuestion("");
    try {
      const result = await api.fleetAsk(value, folder);
      if (mounted.current) setMessages(previous => [...previous, result.exchange]);
    } catch (cause) {
      if (mounted.current) { setError((cause as Error).message); setQuestion(current => current || value); }
    } finally {
      sending.current = false;
      if (mounted.current) { setPending(null); input.current?.focus(); }
    }
  }
  return <div className="rd-folder-chat">
    <div className="rd-folder-chat-heading"><h2>Ask this folder</h2><span>Answers only</span></div>
    <p className="rd-folder-intro">Answers from {folder} and its subfolders{desktop() ? " on this Mac" : ""}.</p>
    <div ref={log} className="rd-folder-conversation" aria-live="polite" aria-label="Folder conversation">
      {!loaded && !error && <p role="status">Loading conversation…</p>}
      {loaded && messages.length === 0 && !pending && <p className="rd-folder-intro">Ask what needs your attention or what the sessions in this folder are working on.</p>}
      {messages.map((message, index) => <div className="rd-folder-exchange" key={`${message.at}-${index}`}><div className="rd-folder-question"><small>You</small><p>{message.q}</p></div><div className="rd-folder-answer"><small>Folder answer</small><div dangerouslySetInnerHTML={{ __html: html(message.a) }} /></div></div>)}
      {pending !== null && <div className="rd-folder-exchange"><div className="rd-folder-question"><small>You</small><p>{pending}</p></div><p role="status">Reading this folder’s sessions…</p></div>}
    </div>
    {error && <div className="rd-folder-error" role="alert">{error}{!loaded && <button className="rd-btn rd-btn-sm" onClick={() => setRetry(n => n + 1)}>Retry</button>}</div>}
    <div className="rd-folder-suggestions">{["What needs my attention?", "What is this folder working on?"].map(text => <button key={text} disabled={!loaded || pending !== null} onClick={() => void ask(text)}>{text}</button>)}</div>
    <form className="rd-folder-compose" onSubmit={event => { event.preventDefault(); void ask(); }}>
      <textarea ref={input} aria-label="Ask this folder" placeholder="Ask about this folder…" rows={2} maxLength={8000} value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void ask(); }
      }} />
      <div><span>Only this folder’s sessions</span><button className="rd-btn rd-btn-primary rd-btn-sm" disabled={!loaded || pending !== null || !question.trim()}>Ask</button></div>
    </form>
    <p className="rd-folder-helper">This chat answers questions. It doesn’t send tasks to agents.</p>
  </div>;
}
