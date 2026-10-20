import { useCallback, useEffect, useState } from "react";
import { desktop, destinationRequest } from "./desktop";
import { clearRemoteGroups, remoteGroups, splitSessionRef } from "./hostTransport";
import { Button, inputStyle, Modal } from "./ui";
import "./backup.css";

type Preview = {
  plan_id: string; coordinator: string; source: string;
  sessions: { host: string; key: string; name: string; folder: string }[];
};

type Status = {
  enabled?: boolean; coordinator?: boolean; error?: string; last_sync?: number;
  configured_coordinator?: string; disconnecting?: { id: string }; unsent_count?: number;
  conflict?: { path: string; token: string };
  pending_changes?: { operation: { id: string; action: string; old: string; new: string }; attempted: boolean; result: { state: string; error?: string } | null }[];
};

function preserveUnavailable(previous: Record<string, Status>, values: Record<string, Status>) {
  return Object.fromEntries(Object.entries(values).map(([id, status]) => [id,
    status.enabled === undefined && status.error ? { ...previous[id], ...status } : status]));
}

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
  const [statuses, setStatuses] = useState<Record<string, Status>>({});
  const [showSetup, setShowSetup] = useState(false);
  const [disconnect, setDisconnect] = useState<string | null>(null);
  const [keepNames, setKeepNames] = useState<Record<string, string>>({});
  const refresh = useCallback(async () => {
    if (!desktop()?.canCollaborate) return;
    const values = await Promise.all((desktop()?.targets ?? []).map(async target => {
      try { return [target.id, await destinationRequest<Status>(target.id, "collaboration-status")] as const; }
      catch (e) { return [target.id, { error: (e as Error).message }] as const; }
    }));
    return Object.fromEntries(values);
  }, []);
  useEffect(() => {
    let closed = false, active = false;
    const load = async () => {
      if (active) return;
      active = true;
      try { const values = await refresh(); if (!closed && values) setStatuses(previous => preserveUnavailable(previous, values)); }
      finally { active = false; }
    };
    void load();
    const timer = setInterval(() => void load(), 2500);
    return () => { closed = true; clearInterval(timer); };
  }, [refresh]);
  const hasConnection = Object.values(statuses).some(s => s.enabled);
  async function recover(target: string, operation: "collaboration-retry" | "collaboration-cancel" | "collaboration-keep-separately" | "collaboration-disconnect", params = {}) {
    setBusy(true); setError("");
    try {
      const result = await destinationRequest<{ error?: string }>(target, operation, params);
      if (result.error) setError(result.error);
      if (operation === "collaboration-disconnect") { clearRemoteGroups(target); setDisconnect(null); }
      window.dispatchEvent(new Event("remote-sessions-refresh"));
    } catch (e) { setError((e as Error).message); }
    finally { const values = await refresh(); if (values) setStatuses(previous => preserveUnavailable(previous, values)); setBusy(false); }
  }

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
      clearRemoteGroups(source); clearRemoteGroups(coordinator);
      setConnected(true); setPreview(null); setShowSetup(false);
      const values = await refresh(); if (values) setStatuses(previous => preserveUnavailable(previous, values));
      window.dispatchEvent(new Event("remote-sessions-refresh"));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <Modal title="Collaboration" onClose={onClose} width={660}>
    <div className="rd-backup">
      <p>Sessions in the same sidebar folder can discover and message one another across connected computers. Ungrouped sessions stay private.</p>
      {!desktop()?.canCollaborate ? <p role="alert">Open an updated DuckTerm Mac app to connect computers.</p> : <>
        {hasConnection && <section className="rd-backup-result" aria-label="Connected computers">
          <strong>Connected computers</strong>
          {targets.map(target => {
            const status = statuses[target.id];
            if (!status?.enabled && !status?.error) return null;
            const current = status.enabled && !status.error && !!status.last_sync && Date.now() - status.last_sync < 60_000;
            return <div key={target.id} className="rd-collaboration-computer">
              <div><strong>{target.name}</strong><p>{status.coordinator ? "Always-on coordinator" : target.id === "local" ? "Keep the app open to receive messages here." : "Receives messages while this computer is running."}</p>
                <p>{status.disconnecting ? "Disconnect pending — retry to finish" : status.error || (current ? "Connected" : status.enabled ? "Waiting for synchronization" : "Unavailable")}</p></div>
              {status.enabled && !status.coordinator && <Button variant="ghost" disabled={busy} onClick={() => setDisconnect(target.id)}>{status.disconnecting ? "Finish disconnect…" : "Disconnect…"}</Button>}
            </div>;
          })}
          <Button variant="ghost" disabled={busy} onClick={() => setShowSetup(true)}>Connect another computer…</Button>
        </section>}
        {targets.map(target => {
          const status = statuses[target.id];
          return <div key={target.id}>
            {status?.pending_changes?.map(item => <section key={item.operation.id} className="rd-backup-result" aria-label="Pending folder change">
              <strong>Pending folder change · {target.name}</strong>
              <p>{item.operation.action === "create" ? `Create ${item.operation.new}` : item.operation.action === "delete" ? `Delete ${item.operation.old}` : `${item.operation.old} → ${item.operation.new}`}</p>
              <p role="status">{item.result?.state === "blocked" ? item.result.error : item.result?.state === "committed" ? "Saved on the coordinator; applying to this computer" : item.attempted ? "Delivery unconfirmed — retry to check the result" : "Waiting for coordinator"}</p>
              <p>The change is saved. Sharing from the affected folder is paused until it is confirmed.</p>
              <div className="rd-collaboration-actions">
                <Button disabled={busy || item.result?.state === "blocked"} onClick={() => void recover(target.id, "collaboration-retry")}>Retry now</Button>
                <Button variant="ghost" disabled={busy || item.result?.state === "committed" || (item.attempted && item.result?.state !== "blocked")} onClick={() => void recover(target.id, "collaboration-cancel", { id: item.operation.id })}>Cancel pending change</Button>
              </div>
              {item.result?.state === "blocked" && <p>Cancel this rejected change, then review the current folder arrangement before trying again.</p>}
            </section>)}
            {status?.conflict && <section className="rd-backup-result" aria-label="Folder needs attention">
              <strong>Folder needs attention · {target.name}</strong>
              <p>A local folder named <b>{status.conflict.path}</b> already exists. Its sessions and saved folder content will stay together.</p>
              <label htmlFor={`keep-${target.id}`}>Keep the existing local folder as</label>
              <input id={`keep-${target.id}`} style={inputStyle} disabled={busy} value={keepNames[target.id] ?? `${status.conflict.path.split("/").at(-1)} (local)`} onChange={e => setKeepNames({ ...keepNames, [target.id]: e.target.value })} />
              <div className="rd-collaboration-actions">
                <Button disabled={busy} onClick={() => void recover(target.id, "collaboration-keep-separately", { token: status.conflict!.token, new: keepNames[target.id] ?? `${status.conflict!.path.split("/").at(-1)} (local)` })}>Keep separately and retry</Button>
                <Button variant="ghost" disabled={busy} onClick={() => void recover(target.id, "collaboration-retry")}>Retry without changes</Button>
              </div>
            </section>}
          </div>;
        })}
        {disconnect && <section className="rd-backup-result" aria-label="Confirm disconnect">
          <strong>Disconnect {targets.find(t => t.id === disconnect)?.name}?</strong>
          <p>Remote agents keep collaborating. This computer’s sessions and files stay in place. Cross-computer discovery and delivery stop for this connection.</p>
          <p>{statuses[disconnect]?.unsent_count ?? 0} unsent actions will be closed. Already delivered messages remain in history.</p>
          <div className="rd-collaboration-actions">
            <Button variant="ghost" disabled={busy} onClick={() => setDisconnect(null)}>{statuses[disconnect]?.disconnecting ? "Back" : "Keep connected"}</Button>
            <Button disabled={busy} onClick={() => void recover(disconnect, "collaboration-disconnect")}>Disconnect and cancel unsent actions</Button>
          </div>
        </section>}
        {(!hasConnection || showSetup) && <>
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
        </>}
        {connected && <p role="status">Computer connected. Sessions in the same folder can now collaborate.</p>}
        {error && <p role="alert">{error}</p>}
        <footer>
          <Button variant="ghost" disabled={busy} onClick={preview ? () => { setPreview(null); setError(""); } : onClose}>{preview ? "Back" : "Close"}</Button>
          {(!hasConnection || showSetup) && <Button disabled={busy || !coordinator || source === coordinator || (source !== "local" && !ssh.trim())} onClick={() => void (preview ? connect() : review())}>
            {busy ? "Connecting…" : preview ? "Connect computers" : "Review sharing"}
          </Button>}
        </footer>
      </>}
    </div>
  </Modal>;
}
