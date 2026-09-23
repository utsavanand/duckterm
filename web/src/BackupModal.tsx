import { useEffect, useRef, useState } from "react";
import { api, BackupState } from "./api";
import { Button, inputStyle, Modal } from "./ui";
import "./backup.css";

export function BackupModal({ onClose }: { onClose: () => void }) {
  const [state, setState] = useState<BackupState | null>(null);
  const [destination, setDestination] = useState("");
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [retry, setRetry] = useState(0);
  const sending = useRef(false);
  const job = state?.job;
  const running = job?.status === "running";

  useEffect(() => {
    let cancelled = false;
    void api.backupStatus().then((result) => {
      if (cancelled) return;
      setState(result);
      setDestination(result.destination ?? "");
      setUncertain(false);
      setError("");
    }).catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [retry]);

  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await api.backupStatus();
        if (!cancelled) { setState(result); setError(""); }
      } catch (e) {
        if (!cancelled) setError(`Could not refresh backup status: ${(e as Error).message}`);
      } finally {
        if (!cancelled) timer = setTimeout(refresh, 1500);
      }
    }
    timer = setTimeout(refresh, 1500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [running]);

  async function start() {
    if (sending.current || running || uncertain || !state || !destination.trim()) return;
    sending.current = true;
    setStarting(true);
    setError("");
    try {
      const result = await api.startBackup(destination.trim());
      setState(result);
      setDestination(result.destination ?? destination);
    } catch (e) {
      setError((e as Error).message);
      // The POST may have reached the server even if its response was lost.
      // Confirm the current job before permitting another start.
      setUncertain(true);
      try {
        setState(await api.backupStatus());
        setUncertain(false);
      } catch { /* Refresh status remains available; do not start blindly. */ }
    } finally {
      sending.current = false;
      setStarting(false);
    }
  }

  return <Modal title="Back up to remote" onClose={onClose}>
    <div className="rd-backup" role="region" aria-label="Backup controls">
      <p>Create a backup now. Choose a local folder or a Google Cloud Storage destination.</p>
      {!state && !error && <p role="status">Loading backup settings…</p>}
      <label htmlFor="backup-destination">Backup destination</label>
      <input id="backup-destination" style={inputStyle} value={destination} disabled={!state || starting || running || uncertain} onChange={(e) => setDestination(e.target.value)} placeholder="/Volumes/Backup/RubberTerm or gs://bucket/prefix" />
      <p className="rd-backup-note">This destination is remembered for the next backup.</p>
      <p>Includes the database, checkpoints, snapshots, and Claude/Codex transcripts. Excludes code, uncommitted files, and credential files.</p>
      <p className="rd-backup-note">Conversation text is preserved as written. Review your destination before uploading.</p>
      {job && <section className={`rd-backup-result rd-backup-${job.status}`} aria-label="Backup result">
        <strong role="status">{running ? "Backing up…" : job.status === "succeeded" ? "Backup complete" : job.status === "interrupted" ? "Backup interrupted" : "Backup failed"}</strong>
        <p>Destination: {job.destination}</p>
        {running && <p>The backup continues if you close this window.</p>}
        {job.status === "succeeded" && <p>Saved to {job.result ?? job.archive_path}</p>}
        {job.error && <p role="alert">{job.error}</p>}
        {job.status !== "succeeded" && job.archive_path && <p>Local archive retained at {job.archive_path}</p>}
      </section>}
      {error && <div role="alert"><p>{error}</p>{uncertain && <p>Confirm the current backup status before starting another backup.</p>}<Button variant="ghost" disabled={starting} onClick={() => setRetry((n) => n + 1)}>Refresh status</Button></div>}
      <footer><Button variant="ghost" onClick={onClose}>{running || job ? "Close" : "Cancel"}</Button><Button disabled={!state || starting || running || uncertain || !destination.trim()} onClick={() => void start()}>{starting ? "Starting…" : running ? "Backing up…" : "Back up now"}</Button></footer>
    </div>
  </Modal>;
}
