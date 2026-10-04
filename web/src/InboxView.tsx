import { useEffect, useRef, useState } from "react";
import { api, InboxFilter, InboxMessage, SessionCard } from "./api";
import { SessionView } from "./types";
import "./inbox.css";

const labels: Record<InboxMessage["status"], string> = {
  queued: "Awaiting reply",
  read: "Read",
  accepted: "Received by session",
  answered: "Answered",
  declined: "Declined",
  expired: "Expired",
  cancelled: "Cancelled",
};

function awaitingReply(message: InboxMessage) {
  const required = message.requires_reply ?? message.kind !== "broadcast";
  return required && (message.status === "queued" || message.status === "accepted" ||
    (message.kind === "broadcast" && message.status === "read"));
}

export function InboxView({ session, folder, onMessageFolder }: ({ session: SessionView; folder?: never } | { session?: never; folder: string }) & { onMessageFolder?: () => void }) {
  const [card, setCard] = useState<SessionCard | null>(null);
  const [messages, setMessages] = useState<InboxMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [totals, setTotals] = useState<Record<InboxFilter, number> | null>(null);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState<number | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [retry, setRetry] = useState(0);
  const [pageCount, setPageCount] = useState(1);
  const [introducing, setIntroducing] = useState(false);
  const [introduced, setIntroduced] = useState(false);
  const [introductionError, setIntroductionError] = useState("");
  const [introductionText, setIntroductionText] = useState("");
  const [filter, setFilter] = useState<InboxFilter>("all");
  const [search, setSearch] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const toolsRef = useRef<HTMLDialogElement>(null);
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
          const page = folder !== undefined ? await api.folderInbox(folder, before, filter) : await api.inbox(sessionKey!, before, filter);
          if (cancelled) return;
          if (i === 0) { setCard(page.card ?? null); setTotals(page.counts ?? null); }
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
  }, [sessionKey, folder, retry, pageCount, filter]);

  const query = search.trim().toLocaleLowerCase();
  const visibleMessages = messages.filter((message) =>
    (filter === "all" || (filter === "pending" ? awaitingReply(message) : message.status === "answered")) &&
    [message.sender_kind === "owner" ? "You" : message.sender_name, message.recipient_name, message.question, message.answer]
      .filter(Boolean).join(" ").toLocaleLowerCase().includes(query));
  const counts = totals ?? {
    all: messages.length,
    pending: messages.filter(awaitingReply).length,
    answered: messages.filter((message) => message.status === "answered").length,
  };

  function changeFilter(value: InboxFilter) {
    if (value !== filter) {
      setFilter(value);
      setPageCount(1);
      setCursor(null);
      setLoaded(false);
      setLoadingOlder(false);
      setError("");
    }
    resetScroll();
  }

  function resetScroll() {
    if (listRef.current) listRef.current.scrollTop = 0;
  }

  return (
    <div className={`rd-inbox${folder !== undefined ? " rd-folder-inbox" : ""}`} aria-label={`${folder ?? session?.label} ${folder !== undefined ? "interactions" : "inbox"}`}>
      <header className="rd-inbox-heading">
        <div>
          <h2>{folder !== undefined ? "Session interactions" : "Inbox"}</h2>
          <p>{folder !== undefined ? `${folder} · Messages and replies in this folder and its subfolders.` : <>Messages to <strong>{session?.label}</strong>{card?.folder && ` · ${card.folder}`}</>}</p>
        </div>
        {onMessageFolder && <button className="rd-btn rd-btn-primary rd-btn-sm" onClick={onMessageFolder}>Message folder</button>}
      </header>
      {card && (
        <details className="rd-session-card">
          <summary>
            <div className="rd-card-title"><span>Session card</span><strong>{card.name || session?.label}</strong></div>
            <div className="rd-card-brief"><p title={card.purpose}>{card.purpose || "No purpose published yet"}</p><small>Current work &amp; next steps</small></div>
            <span className="rd-card-toggle">Details <span aria-hidden="true">›</span></span>
          </summary>
          <div className="rd-card-content" tabIndex={0} role="region" aria-label="Session card details">
            {card.purpose && <p>{card.purpose}</p>}
            {card.activity !== card.purpose && <p className="rd-card-activity">{card.activity}</p>}
            <dl>
              <dt>Session ID</dt><dd>{card.api_name}</dd>
              <dt>Folder</dt><dd>{card.folder || "Ungrouped"}</dd>
              <dt>Shared scope</dt><dd>{card.root || "Private"}</dd>
              {card.cwd && <><dt>Workspace</dt><dd>{card.cwd}</dd></>}
            </dl>
            {card.next_actions.length > 0 && <><h3>Next steps</h3><ul>{card.next_actions.map((action, i) => <li key={i}>{action}</li>)}</ul></>}
            <time dateTime={new Date(card.updated_at).toISOString()}>Updated {new Date(card.updated_at).toLocaleString()}</time>
          </div>
        </details>
      )}
      <section className="rd-inbox-mail" aria-label="Received messages">
        <div className="rd-inbox-toolbar">
          <div className="rd-inbox-filters" role="group" aria-label="Filter messages">
            {([['all', 'All'], ['pending', 'Awaiting reply'], ['answered', 'Answered']] as const).map(([value, label]) => (
              <button key={value} aria-pressed={filter === value} onClick={() => changeFilter(value)}>
                {label} <span>{loaded ? `${counts[value]}${!totals && cursor !== null ? "+" : ""}` : ""}</span>
              </button>
            ))}
          </div>
          <input type="search" aria-label="Search loaded messages" placeholder="Search loaded messages" value={search} onChange={(event) => { setSearch(event.target.value); resetScroll(); }} />
        </div>
        {error && (
          <div className="rd-inbox-error" role="alert">
            <span>Could not refresh {folder !== undefined ? "interactions" : "inbox"}: {error}</span>
            <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => setRetry((n) => n + 1)}>Retry</button>
          </div>
        )}
        <div className="rd-inbox-columns" aria-hidden="true"><span>Sender{folder !== undefined ? " → recipient" : ""}</span><span>Message</span><span>Status</span><span>Received</span></div>
        <div className="rd-inbox-list" ref={listRef} tabIndex={0} role="region" aria-label="Scrollable inbox">
          {!loaded && !error && <p className="rd-inbox-empty" role="status">Loading inbox…</p>}
          {loaded && visibleMessages.length === 0 && (
            <div className="rd-inbox-empty">
              <h3>{messages.length === 0 ? "No messages yet" : "No messages match this view"}</h3>
              <p>{messages.length === 0 ? "Messages will appear here with their sender and reply status." : "Try another filter or search. Search covers loaded messages only."}</p>
            </div>
          )}
          {visibleMessages.map((message) => (
            <details className={`rd-inbox-message${awaitingReply(message) ? " rd-inbox-pending" : ""}`} key={message.id}>
              <summary>
                <div className="rd-inbox-message-head">
                  <strong title={message.sender}>{message.sender_kind === "owner" ? "You" : message.sender_name}</strong>
                  <small className={message.sender_kind === "owner" ? "rd-inbox-owner" : ""}>{message.sender_kind === "owner" ? "Owner" : "Session"}</small>
                  {folder !== undefined && <small className="rd-inbox-recipient">→ {message.recipient_name ?? message.recipient}</small>}
                </div>
                <p className="rd-inbox-preview">{message.question}</p>
                <span className={`rd-inbox-status rd-inbox-status-${message.status}`}>
                  {awaitingReply(message) && message.kind === "broadcast" ? "Awaiting reply" : message.kind === "broadcast" && message.status === "queued" ? "Unread" : labels[message.status]}
                </span>
                <time className="rd-inbox-received" dateTime={new Date(message.created_at).toISOString()} title={new Date(message.created_at).toLocaleString()}>
                  {new Date(message.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                  <small>{new Date(message.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}</small>
                </time>
              </summary>
              <div className="rd-inbox-body">
                <div className="rd-inbox-sender">{message.sender_kind === "owner" ? "From you" : `From session: ${message.sender}`}</div>
                <h3>{message.kind === "broadcast" ? "Message" : "Question"}</h3>
                <p>{message.question}</p>
                {message.answer !== null && (
                  <section className="rd-inbox-answer">
                    <h3>{message.status === "declined" ? "Reason declined" : "Reply"}</h3>
                    <p>{message.answer}</p>
                    {message.answered_at !== null && <time dateTime={new Date(message.answered_at).toISOString()}>{new Date(message.answered_at).toLocaleString()}</time>}
                  </section>
                )}
                <div className="rd-inbox-delivery">
                  <time dateTime={new Date(message.created_at).toISOString()}>Received {new Date(message.created_at).toLocaleString()}</time>
                  {message.delivery && <span>{message.delivery.last_read_at ? "Read by session" : message.delivery.outcome === "notified" ? "Notice shown · Not yet read" : "Not yet read"}</span>}
                  {message.requires_reply === false && <span>No reply required</span>}
                </div>
              </div>
            </details>
          ))}
        </div>
        <footer className="rd-inbox-footer">
          <span role="status">{loaded ? `${visibleMessages.length} of ${messages.length} loaded messages` : ""}</span>
          {cursor !== null ? (
            <button className="rd-inbox-more" disabled={loadingOlder} onClick={() => { setLoadingOlder(true); setPageCount((n) => n + 1); }}>
              {loadingOlder ? "Loading…" : "Load older messages"}
            </button>
          ) : <span className="rd-inbox-read-hint">Newest first · Select a message to read</span>}
        </footer>
      </section>
      {folder === undefined && <footer className="rd-inbox-page-footer"><button onClick={() => toolsRef.current?.showModal()}>Session tools &amp; help</button></footer>}
      {folder === undefined && (
        <dialog ref={toolsRef} className="rd-inbox-tools" aria-labelledby="inbox-tools-title">
          <header><h2 id="inbox-tools-title">Session tools</h2><button className="rd-btn rd-btn-ghost rd-btn-sm" aria-label="Close session tools" onClick={() => toolsRef.current?.close()}>✕</button></header>
          <p className="rd-inbox-help">The agent can check its inbox when ready with <code>duckterm session inbox</code>. You can ask it to run this in its terminal.</p>
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
        </dialog>
      )}
    </div>
  );
}
