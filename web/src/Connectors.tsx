import { useEffect, useState } from "react";
import { api, Connector } from "./api";
import { useToast } from "./ui";

const sourceNames: Record<string, string> = {
  "gh-cli": "GitHub CLI login on this computer",
  stored: "Stored API credentials",
  anonymous: "Public access (no token)",
  "google-oauth": "Google OAuth on this computer",
  "gcloud-cli": "Google Cloud CLI account",
  "railway-cli": "Railway CLI login on this computer",
};

export function Connectors() {
  const toast = useToast();
  const [rows, setRows] = useState<Connector[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [write, setWrite] = useState(false);

  const refresh = () => { api.connectors().then(d => setRows(d.connectors)).catch(() => undefined); };
  useEffect(() => {
    refresh();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, []);

  function edit(c: Connector) {
    setEditing(c.name);
    setSource(c.credential ?? (c.sources?.length === 1 ? c.sources[0] : ""));
    setWrite(c.write_access);
    setToken("");
    setSecret("");
  }

  async function change(c: Connector, action: "enable" | "disable" | "forget") {
    if (action === "forget" && !window.confirm(`Forget credentials stored by Duckterm for ${c.title}? This also disables the connector. CLI logins and provider authorization remain active.`)) return;
    setBusy(c.name);
    try {
      const next = action === "enable"
        ? await api.enableConnector(c.name, editing === c.name ? token.trim() || undefined : undefined, editing === c.name ? secret.trim() || undefined : undefined, source, write)
        : action === "forget" ? await api.forgetConnector(c.name) : await api.disableConnector(c.name);
      setRows(rs => rs.map(r => r.name === next.name ? next : r));
      setEditing(null);
      setToken("");
      setSecret("");
      toast(action === "enable" ? `${c.title} enabled — available to new agent sessions` : `${c.title} disabled in Duckterm${action === "forget" ? "; stored credentials removed" : "; authorization retained"}`);
    } catch (e) {
      // Clear submitted secrets even when validation fails.
      setToken("");
      setSecret("");
      toast(`${c.title}: ${(e as Error).message}`, "err");
    } finally {
      setBusy(null);
    }
  }

  return <div className="rd-connectors">
    <div className="rd-panel-head"><span>Connectors ({rows.length}) · this computer</span><button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={refresh}>Refresh</button></div>
    <div className="rd-connector-desc">Enabled integrations are shared by agents on this computer.</div>
    {rows.map(c => <div key={c.name} className="rd-connector">
      <div className="rd-connector-row">
        <span className={`dot ${c.enabled ? "on" : "off"}`} />
        <span className="rd-connector-title">{c.title}</span>
        <span className="rd-connector-cred">{c.credential ? sourceNames[c.credential] ?? c.credential : "Not connected"}</span>
        {(!c.managed || c.hosted === false) && <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => c.enabled ? change(c, "disable") : c.managed ? change(c, "enable") : edit(c)}>{c.enabled ? "Disable" : "Connect"}</button>}
      </div>
      <div className="rd-connector-desc">{c.description}{c.name === "porkbun" ? ` · ${c.write_access ? "Write access enabled" : "Read-only"}` : ""}</div>
      <div className="rd-connector-desc">{c.identity ?? (c.credential ? "Identity not verified — reconnect to verify" : c.detail)}</div>
          {!c.managed && c.name === "gmail" && !c.ready && (
            <details className="rd-connector-desc">
              <summary>Set up personal Gmail</summary>
              <p>On the connector host, enable the Gmail API in your Google Cloud project.
                Configure an External OAuth consent screen with your Gmail address as a test user,
                then create a Desktop app OAuth client.</p>
              <p>Save the downloaded client JSON as <code>~/.gmail-mcp/gcp-oauth.keys.json</code>.</p>
              <p>Run <code>duckterm connector-auth gmail</code> and sign in with Google.
                This connector requests read-only mail access.</p>
              <p>Then click Refresh and Connect.</p>
              <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">
                Open Google Cloud credentials
              </a>
            </details>
          )}
          {!c.managed && c.name === "gcp" && !c.ready && (
            <details className="rd-connector-desc">
              <summary>Set up Google Cloud</summary>
              <p>Install the Google Cloud CLI on the connector host, then run
                <code> gcloud auth login</code> and
                <code> gcloud config set project YOUR_PROJECT_ID</code>.</p>
              <p>Then click Refresh and Connect. Tools use the active account's permissions.</p>
              <a href="https://cloud.google.com/sdk/docs/install" target="_blank" rel="noreferrer">
                Google Cloud CLI installation
              </a>
            </details>
          )}
      {c.managed ? <div className="rd-connector-desc">Managed through the remote connector administrator.</div> : <>
        {c.credential && <div className="rd-connector-installed">
          <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => edit(c)}>Change connection</button>
          <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => change(c, "forget")}>Forget stored credentials</button>
          <a href={c.revoke_url} target="_blank" rel="noreferrer">Revoke at {c.title} ↗</a>
          <div>Revocation must be completed on the provider’s website. Disable does not revoke authorization.</div>
        </div>}
        {editing === c.name && <div className="rd-connector-token">
          <label>Credential source <select aria-label={`${c.title} credential source`} value={source} onChange={e => { setSource(e.target.value); setToken(""); setSecret(""); }}>
            <option value="" disabled>Choose a source</option>
            {(c.sources ?? []).map(s => <option key={s} value={s}>{sourceNames[s] ?? s}</option>)}
          </select></label>
          {source === "stored" && <>
            <input aria-label={`${c.title} API key`} type="password" autoComplete="off" value={token} placeholder={c.credential === "stored" ? "Leave blank to reuse stored credentials" : "API key or personal access token"} onChange={e => setToken(e.target.value)} />
            {c.name === "porkbun" && <input aria-label="Porkbun secret key" type="password" autoComplete="off" value={secret} placeholder="Secret key" onChange={e => setSecret(e.target.value)} />}
          </>}
          {c.name === "porkbun" && <label><input type="checkbox" checked={write} onChange={e => setWrite(e.target.checked)} />Allow changes to domains and DNS records</label>}
          <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={busy !== null || !source} onClick={() => change(c, "enable")}>Verify and enable</button>
          <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => { setEditing(null); setToken(""); setSecret(""); }}>Cancel</button>
        </div>}
      </>}
    </div>)}
  </div>;
}
