import { useCallback, useEffect, useState } from "react";
import "./updateDuckTerm.css";
export type AppUpdateState = {
  phase: "idle" | "checking" | "available" | "current" | "downloading" | "verifying" | "restarting" | "succeeded" | "failed" | "unavailable";
  installed_version: string; latest_version?: string; can_cancel: boolean; progress?: number; error?: string;
};
export function hasAppUpdater(): boolean {
  return window.__rubbertermDesktop?.currentTarget === "local" && window.__rubbertermDesktop.canUpdateApp === true && !!window.webkit?.messageHandlers?.appUpdate;
}
async function request(operation: string): Promise<AppUpdateState> {
  if (!hasAppUpdater()) throw new Error("Open this Mac’s local dashboard to update the app.");
  return await window.webkit!.messageHandlers!.appUpdate!.postMessage({ operation }) as AppUpdateState;
}
export function NativeAppUpdate() {
  const [state, setState] = useState<AppUpdateState | null>(null), [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  useEffect(() => {
    let active = true;
    const receive = (value: AppUpdateState) => { if (active) setState(value); };
    const event = (e: Event) => receive((e as CustomEvent<AppUpdateState>).detail);
    window.addEventListener("app-update", event);
    void request("status").then(async value => {
      receive(value);
      if (active && ["idle", "current"].includes(value.phase)) receive(await request("check"));
    }).catch(() => { if (active) setError("Couldn’t contact the app updater. Reopen Settings to retry."); });
    // The native operation survives closing Settings. Polling restores progress
    // if a WebKit event was lost; it never initiates an update or rechecks feeds.
    const timer = window.setInterval(() => { void request("status").then(receive).catch(() => {}); }, 2000);
    return () => { active = false; clearInterval(timer); window.removeEventListener("app-update", event); };
  }, []);
  const run = useCallback(async (operation: string) => {
    setPending(true); setError("");
    try { setState(await request(operation)); }
    catch { setError("The update action could not complete. Check again."); }
    finally { setPending(false); }
  }, []);
  const phase = state?.phase ?? "checking";
  const busy = ["checking", "downloading", "verifying", "restarting"].includes(phase);
  const label = phase === "checking" ? "Checking for updates…" : phase === "downloading" ? "Downloading update…" : phase === "verifying" ? "Verifying and preparing update…" : "Restarting DuckTerm…";
  return <section className="rd-update-control" aria-label="Update DuckTerm">
    <h3>Update DuckTerm</h3>
    {state && !busy && <p className="rd-update-version">Installed {state.installed_version}
      {phase === "available" && <span>{state.latest_version} available</span>}
      {phase === "current" && <span>Up to date</span>}
      {phase === "succeeded" && <span>Updated to {state.installed_version}</span>}
    </p>}
    {!busy && <p>Updates the complete Mac app and bundled runtime.</p>}
    {(error || state?.error) && <p role="alert">{error || state?.error}</p>}
    {busy && <div role="status">
      <p>{label}{phase === "downloading" && state?.progress !== undefined ? ` ${Math.round(state.progress * 100)}%` : ""}</p>
      {phase !== "checking" && <progress aria-label={label} max={1} value={state?.progress} />}
      {phase === "restarting" && <p>Reconnecting to your sessions.</p>}
    </div>}
    <div className="rd-update-actions">
      {phase === "available" && <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={pending} onClick={() => void run("install")}>Update &amp; restart</button>}
      {!busy && phase !== "unavailable" && <button className="rd-btn rd-btn-sm" disabled={pending} onClick={() => void run("check")}>{phase === "failed" ? "Retry" : "Check again"}</button>}
      {busy && state?.can_cancel && <button className="rd-btn rd-btn-sm" disabled={pending} onClick={() => void run("cancel")}>Cancel</button>}
    </div>
  </section>;
}
