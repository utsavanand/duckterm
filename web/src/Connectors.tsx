import { hostName } from "./hostTransport";
import { useCallback, useEffect, useState } from "react";
import { api, Connector, ConnectorCheck } from "./api";
import { useToast } from "./ui";

// Hook-derived, so silence means "nothing reported it", never "never used".
function lastUsedLabel(c: Connector): string {
  if (!c.last_used) return "No recorded use";
  const minutes = Math.round((Date.now() - c.last_used) / 60000);
  if (minutes < 1) return `Used just now · ${c.use_count} calls`;
  if (minutes < 60) return `Used ${minutes}m ago · ${c.use_count} calls`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `Used ${hours}h ago · ${c.use_count} calls`;
  return `Used ${Math.round(hours / 24)}d ago · ${c.use_count} calls`;
}

const HARNESS_ORDER = ["claude-code", "codex"];
const harnessNames: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex" };

// A registration is written whether or not the agent CLI exists here, so say
// which agents can actually use it rather than that a config entry was written.
function availabilityLabel(c: Connector): string {
  const chosen = c.harnesses ?? HARNESS_ORDER;
  const named = chosen.map(h => {
    const label = harnessNames[h] ?? h;
    return c.harnesses_present?.[h] === false ? `${label} (not installed here)` : label;
  });
  const omitted = HARNESS_ORDER.filter(h => !chosen.includes(h)).map(h => harnessNames[h] ?? h);
  const tail = omitted.length ? ` · not ${omitted.join(", ")}` : "";
  return `Available to ${named.join(", ")}${tail}`;
}

const sourceNames: Record<string, string> = {
  "gh-cli": "GitHub CLI login on this computer",
  stored: "Stored API credentials",
  anonymous: "Public access (no token)",
  "google-oauth": "Google OAuth on this computer",
  "gcloud-cli": "Google Cloud CLI account",
  "railway-cli": "Railway CLI login on this computer",
};

export function Connectors({ sessionKey = "" }: { sessionKey?: string }) {
  const toast = useToast();
  const [rows, setRows] = useState<Connector[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [write, setWrite] = useState(false);
  const [harnesses, setHarnesses] = useState<string[]>(HARNESS_ORDER);
  const [checks, setChecks] = useState<Record<string, ConnectorCheck & { at: number }>>({});

  async function verify(c: Connector) {
    setBusy(c.name);
    try {
      const result = await api.verifyConnector(c.name, sessionKey);
      setChecks(prev => ({ ...prev, [c.name]: { ...result, at: Date.now() } }));
    } catch (e) {
      toast(`${c.title}: ${(e as Error).message}`, "err");
    } finally {
      setBusy(null);
    }
  }

  const refresh = useCallback(() => { api.connectors(sessionKey).then(d => setRows(d.connectors)).catch(() => undefined); }, [sessionKey]);
  useEffect(() => {
    refresh();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refresh]);

  function edit(c: Connector) {
    setEditing(c.name);
    setSource(c.credential ?? (c.sources?.length === 1 ? c.sources[0] : ""));
    setWrite(c.write_access);
    setHarnesses(c.harnesses ?? HARNESS_ORDER);
    setToken("");
    setSecret("");
  }

  async function change(c: Connector, action: "enable" | "disable" | "forget") {
    if (action === "forget" && !window.confirm(`Forget credentials stored by Duckterm for ${c.title}? This also disables the connector. CLI logins and provider authorization remain active.`)) return;
    setBusy(c.name);
    try {
      const next = action === "enable"
        ? await api.enableConnector(c.name, editing === c.name ? token.trim() || undefined : undefined, editing === c.name ? secret.trim() || undefined : undefined, source, write, sessionKey, editing === c.name ? harnesses : undefined)
        : action === "forget" ? await api.forgetConnector(c.name, sessionKey) : await api.disableConnector(c.name, sessionKey);
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
    <div className="rd-panel-head"><span>Connectors ({rows.length}) · {hostName(sessionKey)}</span><button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={refresh}>Refresh</button></div>
    <div className="rd-connector-desc">Enabled integrations are shared by agents on {hostName(sessionKey)}.</div>
    {rows.map(c => <div key={c.name} className="rd-connector">
      <div className="rd-connector-row">
        <span className={`dot ${c.enabled ? "on" : "off"}`} />
        <span className="rd-connector-title">{c.title}</span>
        <span className="rd-connector-cred">{c.credential ? sourceNames[c.credential] ?? c.credential : "Not connected"}</span>
        {(!c.managed || c.hosted === false) && <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => c.enabled ? change(c, "disable") : c.managed ? change(c, "enable") : edit(c)}>{c.enabled ? "Disable" : "Connect"}</button>}
      </div>
      <div className="rd-connector-desc">{c.description}{c.name === "porkbun" ? ` · ${c.write_access ? "Write access enabled" : "Read-only"}` : ""}</div>
      <div className="rd-connector-desc">{c.identity ?? (c.credential ? "Identity not verified — reconnect to verify" : c.detail)}</div>
      {c.enabled && <div className="rd-connector-desc rd-connector-proof">
        <span>{checks[c.name]
          ? (checks[c.name].ok
              ? `✓ Verified — ${checks[c.name].tools} tools available`
              : `✗ ${checks[c.name].detail ?? "Verification failed"}`)
          : "Configured · not verified this session"}</span>
        <span> · {lastUsedLabel(c)}</span>
        <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => verify(c)}>
          {busy === c.name ? "Checking…" : "Check now"}
        </button>
      </div>}
      {c.enabled && <div className="rd-connector-desc">{availabilityLabel(c)}</div>}
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
          {c.name === "porkbun" && <label><input type="checkbox" aria-label="Allow changes to domains and DNS records" checked={write} onChange={e => setWrite(e.target.checked)} />Allow changes to domains and DNS records</label>}
          <fieldset className="rd-connector-harnesses">
            <legend>Available to</legend>
            {HARNESS_ORDER.map(h => {
              const present = c.harnesses_present?.[h] ?? true;
              return <label key={h}>
                <input
                  type="checkbox"
                  aria-label={harnessNames[h]}
                  checked={harnesses.includes(h)}
                  onChange={e => setHarnesses(prev =>
                    e.target.checked ? [...HARNESS_ORDER].filter(x => x === h || prev.includes(x))
                                     : prev.filter(x => x !== h))}
                />
                {harnessNames[h]}
                {!present && <span className="dim"> · not installed here</span>}
              </label>;
            })}
          </fieldset>
          <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={busy !== null || !source || harnesses.length === 0} onClick={() => change(c, "enable")}>Verify and enable</button>
          <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => { setEditing(null); setToken(""); setSecret(""); }}>Cancel</button>
        </div>}
      </>}
    </div>)}
  </div>;
}
