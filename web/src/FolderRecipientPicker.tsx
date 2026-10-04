import { useEffect, useRef, useState } from "react";
import { api, BroadcastTarget, FolderRecipients } from "./api";

export interface FolderRecipient {
  identity: string;
  kind: "session" | "folder";
  id: string;
  name: string;
  recipients: BroadcastTarget[];
}

export function FolderRecipientPicker({ folder, query, onPick, onClose }: {
  folder: string; query: string; onPick: (recipient: FolderRecipient) => void; onClose: () => void;
}) {
  const [data, setData] = useState<FolderRecipients | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let cancelled = false;
    void api.folderRecipients(folder).then(result => {
      if (!cancelled) { setData(result); setError(""); }
    }).catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [folder, retry]);
  const options: FolderRecipient[] = data ? [
    ...data.sessions.map(session => ({ identity: data.identity, kind: "session" as const, id: session.session_id, name: session.name, recipients: [session] })),
    ...data.folders.map(child => ({ identity: data.identity, kind: "folder" as const, id: child.path, name: child.name, recipients: child.recipients })),
  ].filter(item => item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase())) : [];
  return <div className="rd-folder-picker" id="folder-recipient-picker" ref={box} role="listbox" aria-label="Sessions and subfolders" onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const buttons = Array.from(box.current?.querySelectorAll<HTMLButtonElement>('button[role="option"]:not(:disabled)') ?? []);
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  }}>
    <div className="rd-folder-picker-label">{folder} · Sessions and subfolders</div>
    {!data && !error && <p role="status">Loading recipients…</p>}
    {error && <p role="alert">{error} <button type="button" onClick={() => setRetry(n => n + 1)}>Retry</button></p>}
    {data && options.length === 0 && <p>No matches in this folder.</p>}
    {options.map(option => {
      const eligible = option.recipients.filter(r => r.eligible).length;
      return <button key={`${option.kind}:${option.id}`} type="button" role="option" aria-selected="false" disabled={!eligible} onClick={() => onPick(option)}>
        <span aria-hidden="true">{option.kind === "folder" ? "▸" : "🦆"}</span>
        <span className="rd-folder-pick-copy"><strong>{option.name}</strong><small>{option.kind === "folder" ? "Subfolder · Review recipients before sending" : option.recipients[0].eligible ? "Session · Reply requested" : option.recipients[0].reason}</small></span>
        <span>{option.kind === "folder" ? `${eligible} ${eligible === 1 ? "session" : "sessions"}` : option.recipients[0].state}</span>
      </button>;
    })}
  </div>;
}
