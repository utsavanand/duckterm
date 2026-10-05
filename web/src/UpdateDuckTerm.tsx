import { useEffect, useState } from "react";
import { authHeaders } from "./api";
import "./updateDuckTerm.css";
export interface UpdateOperation {
  target_version: string;
  phase: "snapshot" | "install" | "restart" | "verify" | "rollback";
  outcome: "running" | "succeeded" | "failed";
  verified: boolean;
  error?: string;
  rollback?: "running" | "succeeded" | "failed";
}
export interface UpdateStatus {
  installed_version: string;
  latest_version: string | null;
  release_url: string;
  update_available: boolean | null;
  install_available: boolean;
  reason: string;
  check_status: "checked" | "failed";
  check_error: string | null;
  operation: UpdateOperation | null;
  backup_running?: boolean;
}
export function UpdateDuckTerm({ onAvailable }: { onAvailable?: (available: boolean) => void } = {}) {
  const [status, setStatus] = useState<UpdateStatus | null>(null), [error, setError] = useState("");
  const [checking, setChecking] = useState(true), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    const abort = new AbortController();
    setChecking(true); setError(""); setStatus(null);
    void fetch("/update/status", { headers: authHeaders(), cache: "no-store", signal: abort.signal }).then(async response => {
      if (!response.ok) throw new Error(response.status === 404 ? "This server does not provide update information yet." : "Couldn’t check for updates.");
      return await response.json() as UpdateStatus;
    }).then(value => { if (active) setStatus(value); }).catch((cause: Error) => { if (active) setError(cause.message); }).finally(() => { if (active) setChecking(false); });
    return () => { active = false; abort.abort(); };
  }, [attempt]);
  useEffect(() => { if (status?.update_available !== null && status?.update_available !== undefined) onAvailable?.(status.update_available); }, [status, onAvailable]);
  const operation = status?.operation;
  const verified = operation?.outcome === "succeeded" && operation.verified && status?.installed_version === operation.target_version;
  const link = status?.release_url.startsWith("https://github.com/utsavanand/duckterm/releases/") ? status.release_url : "https://github.com/utsavanand/duckterm/releases";
  return <section className="rd-update-control" aria-label="Update DuckTerm">
    <h3>Update DuckTerm</h3>
    {checking ? <p role="status">Checking GitHub…</p> : status && <p className="rd-update-version">Installed {status.installed_version}{status.check_status === "checked" && <span>{status.update_available === true ? `${status.latest_version} available` : status.update_available === false ? "Up to date" : `Latest ${status.latest_version}`}</span>}</p>}
    {!checking && (error || status?.check_status === "failed") && <p role="alert">Couldn’t check for updates. {error || status?.check_error}</p>}
    <p>Updates the CLI/server. The Mac app may need a separate download.</p>
    <p>Checks GitHub when you open Updates or choose Check again.</p>
    <div className="rd-update-actions"><button className="rd-btn rd-btn-sm rd-btn-primary" disabled title="Live installation is disabled until the updater is ready">Install update</button><button className="rd-btn rd-btn-sm" disabled={checking} onClick={() => setAttempt(n => n + 1)}>Check again</button></div>
    <p>{status?.backup_running ? "A backup is running. Updates must wait until it finishes." : status?.reason || "In-app installation is not available yet."}</p>
    <a href={link} target="_blank" rel="noreferrer">Release notes and downloads ↗</a>
    {operation && <div className="rd-update-progress" role="status">
      <strong>{verified ? `Updated to ${operation.target_version}` : operation.outcome === "failed" ? "Update failed" : operation.outcome === "succeeded" ? "Awaiting version verification" : `Updating to ${operation.target_version}`}</strong>
      <ol>{["snapshot", "install", "restart", "verify"].map(step => <li key={step} aria-current={operation.phase === step ? "step" : undefined}>{step}</li>)}</ol>
      {operation.error && <p>{operation.error}</p>}
      {operation.rollback && <p>Rollback: {operation.rollback}</p>}
    </div>}
    <p>A future in-app update will take a snapshot first and preserve terminal sessions. Restoring that snapshot during rollback may lose history written after it.</p>
  </section>;
}
