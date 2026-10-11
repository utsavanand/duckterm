import { type ReactNode, useEffect, useRef, useState } from "react";
import "./contextViews.css";

type View = "session" | "connectors";
const views: View[] = ["session", "connectors"];

export function ContextViews({ session, connectors }: { session: ReactNode; connectors: ReactNode }) {
  const [view, setView] = useState<View>(() => {
    try { return localStorage.getItem("rd.contextView") === "connectors" ? "connectors" : "session"; }
    catch { return "session"; }
  });
  useEffect(() => {
    const show = () => setView("session"); window.addEventListener("show-session-notes", show);
    window.addEventListener("duckterm-checkpoint-started", show);
    return () => {
      window.removeEventListener("show-session-notes", show);
      window.removeEventListener("duckterm-checkpoint-started", show);
    };
  }, []);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => {
    try { localStorage.setItem("rd.contextView", view); }
    catch { /* Switching views still works without persistent storage. */ }
  }, [view]);
  return <div className="rd-context-views">
    <div className="rd-context-tabs" role="tablist" aria-label="Context views">
      {views.map((name, index) => <button key={name} ref={el => { tabs.current[index] = el; }}
        id={`context-tab-${name}`} role="tab" aria-selected={view === name}
        aria-controls={`context-view-${name}`} tabIndex={view === name ? 0 : -1}
        onClick={() => setView(name)} onKeyDown={event => {
          let next: number;
          if (event.key === "ArrowRight" || event.key === "ArrowLeft") next = 1 - index;
          else if (event.key === "Home") next = 0;
          else if (event.key === "End") next = 1;
          else return;
          event.preventDefault(); setView(views[next]); tabs.current[next]?.focus();
        }}>{name === "session" ? "Session" : "Connectors"}</button>)}
    </div>
    <div id="context-view-session" className="rd-context-body" role="tabpanel"
      aria-labelledby="context-tab-session" hidden={view !== "session"} tabIndex={0}>{session}</div>
    <div id="context-view-connectors" className="rd-context-connectors-view" role="tabpanel"
      aria-labelledby="context-tab-connectors" hidden={view !== "connectors"} tabIndex={0}>{connectors}</div>
  </div>;
}
