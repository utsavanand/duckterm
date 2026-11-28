import { useEffect, useRef, useState } from "react";
import { api, type ReviewHeader } from "../../api";
import { ChatPanel } from "../ChatPanel";
import type { LineNote } from "./FilesTab";
import type { ReadyRow } from "./readiness";

// The chat panel's two views: questions about the whole PR, and the line
// notes collected for a request-changes review. A readiness fix request opens
// here as a draft addressed to the authoring session or the GitHub PR.
export function ChatRail({ header, notes, draft, onOpenFile, onRequest, onRemoveNote, onDraftDone, onClose }: {
  header: ReviewHeader; notes: LineNote[]; draft: ReadyRow | null;
  onOpenFile: (path: string, line?: number) => void; onRequest: (body: string) => void; onRemoveNote: (index: number) => void;
  onDraftDone: (row: ReadyRow, label: string | null) => void; onClose: () => void;
}) {
  const [view, setView] = useState<"chat" | "notes">("chat");
  useEffect(() => { if (draft) setView("chat"); }, [draft]);
  return <aside className="rv2-chat" aria-label="Review chat">
    <div className="rv2-chattabs" role="group" aria-label="Chat view">
      <button aria-pressed={view === "chat"} onClick={() => setView("chat")}>Chat</button>
      <button aria-pressed={view === "notes"} onClick={() => setView("notes")}>My notes{notes.length ? ` · ${notes.length}` : ""}</button>
      <span className="rv2-sp" />
      <button className="x" aria-label="Close chat" onClick={onClose}>✕</button>
    </div>
    {view === "chat" && draft && <FixDraft key={draft.id} header={header} row={draft} onDone={label => onDraftDone(draft, label)} />}
    {view === "chat" && <div className="rv2-chatbody">
      <ChatPanel header={header} quote="" onClearQuote={() => undefined} onOpenFile={onOpenFile} onDraft={onRequest}
        onCollapse={onClose} pendingQuestion="" onQuestionTaken={() => undefined} />
    </div>}
    {view === "notes" && <div className="rv2-notes">
      {notes.length === 0 ? <p className="rv2-dim">Notes you leave on lines collect here. Request changes sends them as review comments.</p>
        : <ul>{notes.map((note, i) => <li key={i}>
          <button className="rv2-noteloc" onClick={() => onOpenFile(note.path, note.from)}>{note.path}:{note.from}{note.to !== note.from ? `–${note.to}` : ""}</button>
          <p>{note.text}</p>
          <button className="rv2-more" onClick={() => onRemoveNote(i)}>Remove</button>
        </li>)}</ul>}
    </div>}
  </aside>;
}

function FixDraft({ header, row, onDone }: { header: ReviewHeader; row: ReadyRow; onDone: (label: string | null) => void }) {
  const canInbox = !!header.session;
  const canGithub = !!(header.pr && header.github);
  const [dest, setDest] = useState<"inbox" | "github">(canInbox ? "inbox" : "github");
  const [text, setText] = useState(row.ask ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { const el = box.current; if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }, []);
  async function send() {
    setBusy(true); setError("");
    try {
      if (dest === "inbox") {
        await api.reviewSend(header.repo, header.ref, { card: row.card, items: [text.trim()], session: header.session!.key });
        onDone(`Sent to ${header.session!.name} · waiting`);
      } else {
        await api.reviewGithub(header.repo, header.ref, { event: "COMMENT", body: text.trim() });
        onDone(`Commented on #${header.pr!.number}`);
      }
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  return <form className="rv2-draft" aria-label={`Fix request · ${row.name}`} onSubmit={event => { event.preventDefault(); void send(); }}>
    <div className="rv2-dest">
      <span className="rv2-seg" role="group" aria-label="Send to">
        <button type="button" aria-pressed={dest === "inbox"} disabled={!canInbox} onClick={() => setDest("inbox")}>{header.session ? `${header.session.name}'s inbox` : "No authoring session"}</button>
        <button type="button" aria-pressed={dest === "github"} disabled={!canGithub} onClick={() => setDest("github")}>GitHub comment</button>
      </span>
      <button type="button" className="x" aria-label="Discard draft" onClick={() => onDone(null)}>✕</button>
    </div>
    <p className="rv2-dim">{dest === "inbox" ? <>To <b>{header.session?.name}</b>, the session that opened this PR · {row.name}</> : <>Posted on <b>#{header.pr?.number}</b> on GitHub as you · {row.name}</>}</p>
    <textarea ref={box} rows={5} aria-label="Fix request" value={text} onChange={event => setText(event.target.value)} />
    {error && <p className="rv2-note" role="alert">{error}</p>}
    <div className="rv2-fixactions"><button className="primary" disabled={busy || !text.trim()}>{busy ? "Sending…" : dest === "inbox" ? `Send to ${header.session?.name}` : "Post comment"}</button></div>
  </form>;
}
