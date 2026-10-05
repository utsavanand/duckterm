import { useState } from "react";
import { desktop, destinationRequest } from "./desktop";
import { remoteGroups, splitSessionRef } from "./hostTransport";
import { Button, inputStyle, Modal } from "./ui";
import "./backup.css";

type Preview = {
  plan_id: string; coordinator: string; source: string;
  sessions: { host: string; key: string; name: string; folder: string }[];
};

export function CollaborationPanel({ onClose }: { onClose: () => void }) {
  const targets = desktop()?.targets ?? [];
  const remotes = targets.filter(t => t.id !== "local");
  const [coordinator, setCoordinator] = useState(remotes.find(t => t.id === "duckterm-dev")?.id ?? remotes[0]?.id ?? "");
  const [source, setSource] = useState("local");
  const [ssh, setSSH] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);

  async function review() {
    setBusy(true); setError(""); setConnected(false);
    const groups: Record<string, Record<string, string>> = {};
    for (const [ref, folder] of Object.entries(remoteGroups())) {
      const { host, key } = splitSessionRef(ref);
      (groups[host] ??= {})[key] = folder;
    }
    try {
      setPreview(await destinationRequest<Preview>(coordinator, "collaboration-preview", { source, coordinator_ssh: ssh, groups }));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function connect() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      await destinationRequest(coordinator, "collaboration-connect", { source, plan_id: preview.plan_id });
      setConnected(true); setPreview(null);
      window.dispatchEvent(new Event("remote-sessions-refresh"));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <Modal title="Collaboration" onClose={onClose}>
    <div className="rd-backup">
      <p>Sessions in the same sidebar folder can discover and message one another across connected computers. Ungrouped sessions stay private.</p>
      {!desktop()?.canCollaborate ? <p role="alert">Open an updated DuckTerm Mac app to connect computers.</p> : <>
        <label htmlFor="collaboration-coordinator">Always-on coordinator</label>
        <select id="collaboration-coordinator" style={inputStyle} value={coordinator} disabled={busy || !!preview} onChange={e => setCoordinator(e.target.value)}>
          {remotes.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <label htmlFor="collaboration-source">Connect computer</label>
        <select id="collaboration-source" style={inputStyle} value={source} disabled={busy || !!preview} onChange={e => setSource(e.target.value)}>
          <option value="local">This Mac</option>
          {remotes.filter(t => t.id !== coordinator).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        {source !== "local" && <>
          <label htmlFor="collaboration-ssh">Coordinator SSH name on this computer</label>
          <input id="collaboration-ssh" style={inputStyle} value={ssh} disabled={busy || !!preview} onChange={e => setSSH(e.target.value)} placeholder="duckterm-dev" />
          <p>This computer needs its own SSH access to the coordinator. Your Mac’s SSH keys are not copied.</p>
        </>}
        <p>Messages for offline computers wait until they reconnect. Remote agents can keep exchanging messages while your Mac sleeps.</p>
        <p className="rd-backup-note">Keep the Mac app open to receive messages here. Closing it disconnects this Mac.</p>
        {preview && <section aria-label="Folder sharing preview">
          <strong>{preview.source} and {preview.coordinator}</strong>
          <p>The following sidebar arrangement becomes the shared workspace. Future sessions inherit their folder’s access automatically.</p>
          <ul>{preview.sessions.map(s => <li key={`${s.host}:${s.key}`}>{s.name} · {s.host === "local" ? "This Mac" : s.host} — {s.folder || "Ungrouped (private)"}</li>)}</ul>
          <p>The coordinator stores session names, published activity, folder membership and messages you send. Project files and conversation transcripts are not transferred.</p>
        </section>}
        {connected && <p role="status">Computer connected. Sessions in the same folder can now collaborate.</p>}
        {error && <p role="alert">{error}</p>}
        <footer>
          <Button variant="ghost" disabled={busy} onClick={preview ? () => { setPreview(null); setError(""); } : onClose}>{preview ? "Back" : "Close"}</Button>
          <Button disabled={busy || !coordinator || source === coordinator || (source !== "local" && !ssh.trim())} onClick={() => void (preview ? connect() : review())}>
            {busy ? "Connecting…" : preview ? "Connect computers" : "Review sharing"}
          </Button>
        </footer>
      </>}
    </div>
  </Modal>;
}
