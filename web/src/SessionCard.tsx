import { ReactNode, useEffect, useRef, useState } from "react";
import { SessionConversationRecovery } from "./ConversationRecovery";
import { api } from "./api";
import { effectiveState } from "./sessions";
import { SessionView } from "./types";
import { useToast } from "./ui";
import { RestartControls } from "./RestartControls";
import "./sessionCard.css";

// Harness switching and recovery remain visible; routine actions live in the row menu.
export function SessionCard({ session: s, now, notesOpen = false, onCloseNotes }: {
  session: SessionView; now: number; notesOpen?: boolean; onCloseNotes?: () => void;
}) {
  const toast = useToast();
  const state = effectiveState(s, now);
  const archived = ["archived", "merged"].includes(state);
  const live = !["terminated", "stopped", "interrupted", "archived", "merged"].includes(state);
  const [notes, setNotes] = useState(s.notes ?? "");
  const [savedNotes, setSavedNotes] = useState(s.notes ?? "");
  const remoteNotes = useRef(s.notes ?? "");
  const draftNotes = useRef(notes); draftNotes.current = notes;
  useEffect(() => {
    const incoming = s.notes ?? "", previous = remoteNotes.current;
    if (incoming === previous) return;
    remoteNotes.current = incoming;
    if (draftNotes.current === previous) {
      setNotes(incoming); setSavedNotes(incoming);
    } else if (incoming.startsWith(previous)) {
      // A merge appends notes. Preserve an open draft and append the same suffix.
      setNotes(draft => draft + incoming.slice(previous.length));
      setSavedNotes(incoming);
    }
    // Other replacements stay protected by the save-time comparison. Cancel
    // reloads the latest notes without silently replacing an unsaved draft.
  }, [s.notes]);
  const [saving, setSaving] = useState(false);
  const details = (changeHarness?: ReactNode) => <>
    <dl className="rd-session-controls-meta"><div className="rd-session-harness"><dt>Harness</dt><dd><span>{s.runtime ?? "—"}</span>{live && changeHarness}</dd></div>
      <div><dt>Model</dt><dd>{s.model ?? "Not reported yet"}</dd></div></dl>
    {["claude-code", "codex", "copilot"].includes(s.runtime ?? "") && <SessionConversationRecovery key={s.key} session={s} stopped={!live && !archived} />}
  </>;
  return <section className="rd-session-controls" aria-label="Session controls">
    <div className="rd-session-controls-identity">
      <div className="rd-session-controls-caption">Session</div>
      <div className="rd-session-controls-title"><strong>{s.label}</strong><span className={`rd-state st-${state}`}>{state}</span></div>
      <div className="rd-session-controls-folder"><span>{s.group || "Ungrouped"}</span></div>
    </div>
    {s.launched ? <RestartControls session={s} showActions={false}>{details}</RestartControls> : details()}
    {notesOpen && <div className="rd-row-notes-wrap">
      <label htmlFor="session-notes">Notes</label>
      <textarea id="session-notes" autoFocus aria-label="Session notes" className="rd-row-notes" value={notes}
        onChange={e => setNotes(e.target.value)} rows={5} />
      <div className="rd-row-notes-bar"><span className="hint">{notes !== savedNotes ? "Unsaved changes" : "Saved"}</span>
        <button className="rd-btn rd-btn-sm" disabled={saving} onClick={() => { setNotes(s.notes ?? ""); setSavedNotes(s.notes ?? ""); remoteNotes.current = s.notes ?? ""; onCloseNotes?.(); }}>Cancel</button>
        <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={saving || notes === savedNotes} onClick={async () => {
          setSaving(true);
          try { await api.saveNotes(s.key, notes, savedNotes); setSavedNotes(notes); remoteNotes.current = notes; toast("Notes saved"); onCloseNotes?.(); }
          catch (error) { toast(`Notes failed: ${(error as Error).message}`, "err"); }
          finally { setSaving(false); }
        }}>{saving ? "Saving…" : "Save"}</button>
      </div>
    </div>}
  </section>;
}
