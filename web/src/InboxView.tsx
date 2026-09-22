import { useEffect, useState } from "react";
import { api, InboxMessage, SessionCard } from "./api";
import { SessionView } from "./types";
import "./inbox.css";

const labels: Record<InboxMessage["status"], string> = {
  queued: "Awaiting response",
  read: "Read",
  accepted: "Received by session",
  answered: "Answered",
  declined: "Declined",
  expired: "Expired",
  cancelled: "Cancelled",
};

export function InboxView({ session, folder, onMessageFolder }: ({ session: SessionView; folder?: never } | { session?: never; folder: string }) & { onMessageFolder?: () => void }) {
  const [card, setCard] = useState<SessionCard | null>(null);
  const [messages, setMessages] = useState<InboxMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState<number | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [retry, setRetry] = useState(0);
  const [pageCount, setPageCount] = useState(1);
  const [introducing, setIntroducing] = useState(false);
  const [introduced, setIntroduced] = useState(false);
  const [introductionError, setIntroductionError] = useState("");
  const [introductionText, setIntroductionText] = useState("");
  const sessionKey = session?.key;
  const supported = ["codex", "claude-code"].includes(session?.runtime ?? "");

  async function introduce() {
    setIntroducing(true);
    setIntroductionError("");
    try {
      const result = await api.introduceCollaboration(session!.key);
      if (!result.sent) throw new Error("Could not send the introduction. Open the agent's terminal and try again.");
      setIntroduced(true);
    } catch (e) {
      setIntroductionError((e as Error).message);
    } finally {
      setIntroducing(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const incoming: InboxMessage[] = [];
        let before: number | undefined;
        let next: number | null = null;
        for (let i = 0; i < pageCount; i++) {
          const page = folder !== undefined ? await api.folderInbox(folder, before) : await api.inbox(sessionKey!, before);
          if (cancelled) return;
          if (i === 0) setCard(page.card ?? null);
          incoming.push(...page.messages);
          next = page.next_cursor;
          if (next === null) break;
          before = next;
        }
        setMessages(incoming);
        setCursor(next);
        setError("");
        setLoaded(true);
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
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sessionKey, folder, retry, pageCount]);

  return (
    <div className={`rd-inbox${folder !== undefined ? " rd-folder-inbox" : ""}`} aria-label={`${folder ?? session?.label} ${folder !== undefined ? "interactions" : "inbox"}`}>
      <div className="rd-inbox-heading">
        <div>
          <h2>{folder !== undefined ? "Session interactions" : "Inbox"}</h2>
          <p>{folder !== undefined ? "Messages and replies in this folder and its subfolders." : "Messages from you and other sessions, and replies."}</p>
        </div>
        {onMessageFolder && <button className="rd-btn rd-btn-primary rd-btn-sm" onClick={onMessageFolder}>Message folder</button>}
        {loaded && <span className="rd-inbox-count">{messages.length}{cursor !== null ? "+" : ""} {folder !== undefined ? (messages.length === 1 ? "interaction" : "interactions") : "received"}</span>}
      </div>
      {card && (
        <details className="rd-session-card" open>
          <summary>Session card <span>{card.api_name}</span></summary>
          <p>{card.purpose}</p>
          {card.activity !== card.purpose && <p className="rd-card-activity">{card.activity}</p>}
          <dl>
            <dt>Folder</dt><dd>{card.folder || "Ungrouped"}</dd>
            <dt>Shared scope</dt><dd>{card.root || "Private"}</dd>
            {card.cwd && <><dt>Workspace</dt><dd>{card.cwd}</dd></>}
          </dl>
          {card.next_actions.length > 0 && <p><strong>Next:</strong> {card.next_actions.join(" · ")}</p>}
          <time dateTime={new Date(card.updated_at).toISOString()}>Updated {new Date(card.updated_at).toLocaleString()}</time>
        </details>
      )}
      {folder === undefined && <p className="rd-inbox-help">The agent can check its inbox when ready with <code>duckterm session inbox</code>. You can ask it to run this in its terminal.</p>}
      {supported && (
        <div className="rd-inbox-introduction">
          <p>For an existing session, send the agent a guide to discovery, its inbox, and card updates. Use this at an idle prompt with no draft input.</p>
          <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={introducing || introduced || session?.state !== "idle"} onClick={() => void introduce()}>
            {introduced ? "Introduction sent" : introducing ? "Sending…" : "Introduce session collaboration"}
          </button>
          {session?.state !== "idle" && <p>Available when the session is idle.</p>}
          {introduced && <p role="status">Sent to the terminal. This does not confirm the agent has read it yet.</p>}
          {introductionError && <p role="alert">{introductionError}</p>}
          <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => {
            setIntroductionError("");
            void api.collaborationInstructions(session!.key)
              .then((result) => setIntroductionText(result.prompt))
              .catch((e: Error) => setIntroductionError(e.message));
          }}>Show introduction to paste</button>
          {introductionText && <textarea aria-label="Introduction to paste into the agent" readOnly rows={9} value={introductionText} onFocus={(e) => e.currentTarget.select()} />}
        </div>
      )}
      {error && (
        <div className="rd-inbox-error" role="alert">
          <span>Could not refresh {folder !== undefined ? "interactions" : "inbox"}: {error}</span>
          <button onClick={() => setRetry((n) => n + 1)}>Retry</button>
        </div>
      )}
      {!loaded && !error && <p role="status">Loading inbox…</p>}
      {loaded && messages.length === 0 && (
        <div className="rd-inbox-empty">
          <span aria-hidden="true">↙</span>
          <h3>No messages yet</h3>
          <p>
            When another session sends a question, it will appear here with
            the sender and reply status.
          </p>
        </div>
      )}
      <div className="rd-inbox-list">
        {messages.map((message) => (
          <details className="rd-inbox-message" key={message.id}>
            <summary>
              <div className="rd-inbox-message-head">
                <strong title={message.sender}>{message.sender_kind === "owner" ? "You" : message.sender_name}{message.sender_kind === "owner" && <span className="rd-owner-badge">Owner</span>}{folder !== undefined && ` → ${message.recipient_name ?? message.recipient}`}</strong>
                <span className={`rd-inbox-status rd-inbox-status-${message.status}`}>
                  {message.kind === "broadcast" && message.status === "queued" ? "Unread" : labels[message.status]}
                </span>
              </div>
              <p className="rd-inbox-preview">{message.question}</p>
              <time dateTime={new Date(message.created_at).toISOString()}>
                {new Date(message.created_at).toLocaleString()}
              </time>
            </summary>
            <div className="rd-inbox-body">
              <div className="rd-inbox-sender">{message.sender_kind === "owner" ? "From you" : `From session: ${message.sender}`}</div>
              {message.delivery && <p>{message.delivery.last_read_at ? "Read by session" : message.delivery.outcome === "notified" ? "Notice shown · Not yet read" : "Not yet read"}</p>}
              {message.requires_reply === false && <p>No reply required</p>}
              <h3>{message.kind === "broadcast" ? "Message" : "Question"}</h3>
              <p>{message.question}</p>
              {message.answer !== null && (
                <section className="rd-inbox-answer">
                  <h3>{message.status === "declined" ? "Reason declined" : "Reply"}</h3>
                  <p>{message.answer}</p>
                  {message.answered_at !== null && (
                    <time dateTime={new Date(message.answered_at).toISOString()}>
                      {new Date(message.answered_at).toLocaleString()}
                    </time>
                  )}
                </section>
              )}
            </div>
          </details>
        ))}
      </div>
      {cursor !== null && (
        <button className="rd-inbox-more" disabled={loadingOlder} onClick={() => { setLoadingOlder(true); setPageCount((n) => n + 1); }}>
          {loadingOlder ? "Loading…" : "Load older messages"}
        </button>
      )}
    </div>
  );
}
