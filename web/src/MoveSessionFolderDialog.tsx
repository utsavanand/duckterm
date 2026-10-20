import { useState } from "react";
import { createPortal } from "react-dom";
import type { SessionView } from "./types";
import { Modal, useToast } from "./ui";

export function MoveSessionFolderDialog({ session, folders, onMove, onClose }: {
  session: SessionView; folders: string[];
  onMove: (folder: string) => Promise<void>; onClose: () => void;
}) {
  const current = session.group || "";
  const [selected, setSelected] = useState(current);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();
  const choices = ["", ...[...new Set(folders.filter(Boolean))].sort((a, b) => a.localeCompare(b))];
  const visible = choices.filter(folder => (folder || "Ungrouped").toLowerCase().includes(query.trim().toLowerCase()));
  const close = () => { if (!busy) onClose(); };
  return createPortal(<div onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
    if (event.key === "Tab") {
      const items = [...event.currentTarget.querySelectorAll<HTMLElement>("input:not(:disabled),button:not(:disabled)")];
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }}><Modal title="Move to folder" onClose={close}>
    <form className="rd-session-action-dialog rd-move-folder" role="dialog" aria-label={`Move ${session.label} to folder`} aria-modal="true" aria-busy={busy} onSubmit={async event => {
      event.preventDefault();
      if (busy || selected === current || !choices.includes(selected)) return;
      setBusy(true); setError("");
      try { await onMove(selected); toast(selected ? `Moved to ${selected}` : "Removed from folder"); onClose(); }
      catch (e) { setError((e as Error).message); }
      finally { setBusy(false); }
    }}>
      <p><strong>{session.label}</strong> · {current || "Ungrouped"}</p>
      <p>Choose a sidebar folder. Project files and the running agent stay in place. Forks follow their parent.</p>
      <input autoFocus type="search" aria-label="Search folders" placeholder="Search folders" value={query} disabled={busy} onChange={e => setQuery(e.target.value)} />
      <div className="rd-folder-choices" role="group" aria-label="Destination folder">
        {visible.map(folder => <button type="button" key={folder} aria-pressed={selected === folder} disabled={busy} onClick={() => { setSelected(folder); setError(""); }}>
          <span>{folder || "Ungrouped"}</span>{folder === current && <small>Current</small>}
        </button>)}
        {!visible.length && <p>No matching folders.</p>}
      </div>
      <p>Destination: <strong>{selected || "Ungrouped"}</strong></p>
      {error && <p role="alert">Could not move session: {error}</p>}
      <footer><button type="button" className="rd-btn" disabled={busy} onClick={close}>Cancel</button><button className="rd-btn rd-btn-primary" disabled={busy || selected === current || !choices.includes(selected)}>{busy ? "Moving…" : "Move"}</button></footer>
    </form>
  </Modal></div>, document.body);
}
