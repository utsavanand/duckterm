import { useEffect, useRef, useState } from "react";
import { api, InboxMessage } from "./api";
import "./folder-conversations.css";

const statusLabels: Record<InboxMessage["status"], string> = {
  queued: "Awaiting response", accepted: "Accepted · no response yet",
  answered: "Answered", declined: "Declined", cancelled: "Cancelled", expired: "Expired",
};
type Conversation = InboxMessage & { recipient_name: string };

export function FolderConversations({ folder, onClose }: { folder: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [messages, setMessages] = useState<Conversation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState<number | null>(null);
  const [pages, setPages] = useState(1);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const collected: Conversation[] = [];
        let before: number | undefined;
        let next: number | null = null;
        for (let i = 0; i < pages; i++) {
          const result = await api.folderConversations(folder, before);
          if (cancelled) return;
          collected.push(...result.messages);
          next = result.next_cursor;
          if (next === null) break;
          before = next;
        }
        setMessages(collected);
        setCursor(next);
        setLoaded(true);
        setError("");
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) {
          setLoadingOlder(false);
          timer = setTimeout(refresh, 3000);
        }
      }
    }
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [folder, pages, retry]);

  return (
    <dialog ref={dialog} className="rd-folder-conversations" aria-labelledby="folder-conversations-title" onCancel={onClose}>
      <header>
        <div><p className="rd-conversations-folder">{folder}</p><h2 id="folder-conversations-title">Session conversations</h2></div>
        <button autoFocus className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onClose} aria-label="Close conversations">Close</button>
      </header>
      <p className="rd-conversations-description">Messages involving sessions currently in this folder or its subfolders. Newest first; updates appear automatically.</p>
      <p className="rd-conversations-retention">Open requests stay available. Closed conversations are retained for seven days after resolution. Moving a session changes which folder shows its conversations.</p>
      {error && <div role="alert"><p>Could not refresh conversations: {error}</p><button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => setRetry((n) => n + 1)}>Retry</button></div>}
      {!loaded && !error && <p role="status">Loading conversations…</p>}
      {loaded && messages.length === 0 && <p className="rd-conversations-empty">No conversations yet. Questions sent between sessions and their responses will appear here.</p>}
      <div className="rd-conversations-list">
        {messages.map((message) => (
          <details className="rd-conversation" key={message.id}>
            <summary>
              <div className="rd-conversation-participants"><strong title={message.sender}>{message.sender_name}</strong><span aria-label="to">→</span><strong title={message.recipient}>{message.recipient_name}</strong></div>
              <span className={`rd-conversation-status status-${message.status}`}>{message.overdue ? "Overdue · awaiting reply" : statusLabels[message.status]}</span>
              <p className="rd-conversation-preview">{message.question}</p>
              <time dateTime={new Date(message.created_at).toISOString()}>{new Date(message.created_at).toLocaleString()}</time>
            </summary>
            <section><h3>Question</h3><p>{message.question}</p></section>
            {message.answer !== null ? (
              <section className="rd-conversation-reply"><h3>{message.status === "declined" ? "Reason declined" : "Response"}</h3><p>{message.answer}</p>
                {message.answered_at !== null && <time dateTime={new Date(message.answered_at).toISOString()}>{new Date(message.answered_at).toLocaleString()}</time>}
              </section>
            ) : <p className="rd-conversation-no-reply">No response was recorded.</p>}
          </details>
        ))}
      </div>
      {cursor !== null && <button className="rd-btn rd-btn-ghost" disabled={loadingOlder} onClick={() => { setLoadingOlder(true); setPages((n) => n + 1); }}>{loadingOlder ? "Loading…" : "Load older conversations"}</button>}
    </dialog>
  );
}
