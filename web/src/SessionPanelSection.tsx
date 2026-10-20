import { ReactNode, useEffect, useState } from "react";
import "./sessionPanel.css";

export function SessionPanelSection({ title, status, children, initiallyOpen = false, forceOpen = false }: {
  title: string; status?: ReactNode; children: ReactNode; initiallyOpen?: boolean; forceOpen?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen || forceOpen);
  useEffect(() => { if (forceOpen) setOpen(true); }, [forceOpen]);
  return <details className="rd-session-section" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary><span>{title}</span><span className="rd-section-status">{status}</span></summary>
    <div className="rd-session-section-body">{children}</div>
  </details>;
}
