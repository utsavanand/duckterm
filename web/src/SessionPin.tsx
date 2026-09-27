import { useState } from "react";
import { SessionView } from "./types";
import "./sessionFocus.css";

export function SessionPin({ session, onToggle, label = false }: {
  session: SessionView; onToggle: (session: SessionView) => Promise<void>; label?: boolean;
}) {
  const [pending, setPending] = useState(false);
  const action = session.pinned ? "Unpin" : "Pin";
  return <button className={`rd-focus-pin${session.pinned ? " active" : ""}`}
    title={`${action} ${session.label}`} aria-label={`${action} ${session.label}`}
    aria-pressed={!!session.pinned} disabled={pending}
    onClick={(event) => {
      event.stopPropagation();
      if (pending) return;
      setPending(true);
      void onToggle(session).finally(() => setPending(false));
    }}>
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m16 3 5 5-4 1-3 5 1 3-3-1-5 3-1 4-5-5 4-1 3-5-1-3 3 1z" transform="translate(1 -1) scale(.9)" />
    </svg>
    {label && (session.pinned ? "Pinned" : "Pin")}
  </button>;
}
