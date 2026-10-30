import { hostName, sessionRef, splitSessionRef } from "./hostTransport";
import { useCallback, useEffect, useRef, useState } from "react";
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
  "railway-cli": "Railway CLI login on this computer",
};

// Short enough to sit on a collapsed row; the long form lives in Configure.
const sourceTags: Record<string, string> = {
  "gh-cli": "gh CLI login",
  stored: "stored key",
  "railway-cli": "railway CLI login",
};

// One line that answers "can my agents use this right now, and as what".
// A proved tool count beats "configured", which only means a file was written.
function summaryLabel(c: Connector, check: (ConnectorCheck & { at: number }) | undefined): string {
  if (c.managed) return "Managed by your connector host";
  if (!c.enabled) return c.credential || c.stored_secret
    ? "Off — not available to any agent"
    : (c.detail ?? "Not set up");
  if (check && !check.ok) return `Not working — ${check.detail ?? "verification failed"}`;
  // Usage is already known, so lead with it rather than withholding the row
  // behind a check that may be slow, mocked away, or never finish.
  return check ? `${check.tools} tools · ${lastUsedLabel(c)}` : lastUsedLabel(c);
}

export function Connectors({ sessionKey = "" }: { sessionKey?: string }) {
  const toast = useToast();
  const panel = useRef<HTMLDivElement>(null);
  const [rows, setRows] = useState<Connector[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [write, setWrite] = useState(false);
  const [harnesses, setHarnesses] = useState<string[]>(HARNESS_ORDER);
  const [checks, setChecks] = useState<Record<string, ConnectorCheck & { at: number }>>({});
  // Status and usage belong to a host, not its selected session. In-flight CLI
  // probes must not multiply every time the selection or window focus changes.
  const host = splitSessionRef(sessionKey).host;
  const context = host === "local" ? "" : sessionRef(host, "connectors");
  const currentHost = useRef(host);
  currentHost.current = host;
  const pending = useRef<AbortController | null>(null);
  const attempted = useRef<Set<string>>(new Set());
  const revision = useRef(0);
  const lifetime = useRef(0);
  const invalidate = useCallback(() => {
    ++revision.current;
    pending.current?.abort();
    pending.current = null;
  }, []);

  const verifyOne = useCallback(async (c: Connector, announce: boolean) => {
    const epoch = lifetime.current;
    const current = () => lifetime.current === epoch && currentHost.current === host;
    if (announce) setBusy(c.name);
    try {
      const result = await api.verifyConnector(c.name, sessionKey);
      if (!current()) return;
      setChecks(prev => ({ ...prev, [c.name]: { ...result, at: Date.now() } }));
    } catch (e) {
      // An automatic check the owner did not request must not raise a toast;
      // the row says what it found, and Re-check surfaces the message.
      if (current() && announce) toast(`${c.title}: ${(e as Error).message}`, "err");
    } finally {
      if (current() && announce) setBusy(null);
    }
  }, [host, sessionKey, toast]);

  function verify(c: Connector) {
    void verifyOne(c, true);
  }

  const refresh = useCallback(() => {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    const stamp = ++revision.current;
    void api.connectors(context, controller.signal).then(d => {
      if (stamp === revision.current) setRows(d.connectors);
    }).catch(() => undefined).finally(() => {
      if (pending.current === controller) pending.current = null;
    });
  }, [context]);
  useEffect(() => {
    const epoch = ++lifetime.current;
    setRows([]); setChecks({}); setEditing(null); setToken(""); setSecret(""); setBusy(null);
    attempted.current = new Set();
    refresh();
    window.addEventListener("focus", refresh);
    return () => { lifetime.current = epoch + 1; invalidate(); window.removeEventListener("focus", refresh); };
  }, [refresh, invalidate]);

  // Authentication in the embedded terminal does not blur the app window.
  // Check only while this panel is visible and credentials are missing. The
  // shared pending guard coalesces manual, focus and timed refreshes.
  const disconnected = rows.some(c => !c.managed && !c.credential);
  useEffect(() => {
    const visibleRefresh = () => {
      if (document.visibilityState === "visible" && !panel.current?.closest("[hidden]")) refresh();
    };
    document.addEventListener("visibilitychange", visibleRefresh);
    const timer = disconnected ? window.setInterval(visibleRefresh, 30000) : undefined;
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", visibleRefresh); };
  }, [disconnected, refresh]);

  // Verify once per connector per panel lifetime. "Configured" only means a
  // file was written, so the row would otherwise claim nothing useful until
  // the owner clicked. Each check spawns the real server, so the attempted
  // set is tracked separately from results: a failure must not retry forever.
  useEffect(() => {
    for (const c of rows) {
      if (!c.enabled || c.managed || attempted.current.has(c.name)) continue;
      attempted.current.add(c.name);
      void verifyOne(c, false);
    }
  }, [rows, verifyOne]);

  function edit(c: Connector) {
    setEditing(c.name);
    setSource(c.credential ?? (c.sources?.length === 1 ? c.sources[0] : ""));
    setWrite(c.write_access);
    setHarnesses(c.harnesses ?? HARNESS_ORDER);
    setToken("");
    setSecret("");
  }

  // `close` is false for the in-panel availability switch: toggling a setting
  // must leave the panel open, or the owner loses the controls mid-edit.
  async function change(c: Connector, action: "enable" | "disable" | "forget", close = true) {
    const epoch = lifetime.current;
    const current = () => lifetime.current === epoch && currentHost.current === host;
    if (action === "forget" && !window.confirm(`Forget credentials stored by Duckterm for ${c.title}? This also disables the connector. CLI logins and provider authorization remain active.`)) return;
    setBusy(c.name);
    try {
      const next = action === "enable"
        ? await api.enableConnector(c.name, editing === c.name ? token.trim() || undefined : undefined, editing === c.name ? secret.trim() || undefined : undefined, source, write, sessionKey, editing === c.name ? harnesses : undefined)
        : action === "forget" ? await api.forgetConnector(c.name, sessionKey) : await api.disableConnector(c.name, sessionKey);
      if (!current()) return;
      invalidate(); // An older status response cannot undo this confirmed change.
      // A changed credential or permission may serve different tools, so the
      // proved count is stale: drop it and let the effect verify once more.
      attempted.current.delete(c.name);
      setChecks(prev => Object.fromEntries(Object.entries(prev).filter(([k]) => k !== c.name)));
      setRows(rs => rs.map(r => r.name === next.name ? next : r));
      if (close) setEditing(null);
      setToken("");
      setSecret("");
      toast(action === "enable" ? `${c.title} enabled — available to new agent sessions` : `${c.title} disabled in Duckterm${action === "forget" ? "; stored credentials removed" : "; authorization retained"}`);
    } catch (e) {
      if (!current()) return;
      // Clear submitted secrets even when validation fails.
      setToken("");
      setSecret("");
      toast(`${c.title}: ${(e as Error).message}`, "err");
    } finally {
      if (current()) setBusy(null);
    }
  }

  return <div ref={panel} className="rd-connectors">
    <div className="rd-panel-head">
      <span>Connectors · {hostName(sessionKey)}</span>
      <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={refresh}>Refresh</button>
    </div>
    {rows.map(c => {
      const check = checks[c.name];
      const open = editing === c.name;
      const configurable = !c.managed || c.hosted === false;
      return <div key={c.name} className="rd-connector">
        <div className="rd-connector-row">
          <span className={`dot ${c.enabled ? "on" : "off"}`} />
          <div className="rd-connector-main">
            <div className="rd-connector-title">{c.title}</div>
            <div className="rd-connector-summary">
              <span>{summaryLabel(c, check)}</span>
              {c.credential && <span className="rd-connector-tag">{sourceTags[c.credential] ?? c.credential}</span>}
              {/* Write access is the one permission that can destroy the owner's
                  data, so it is named on the collapsed row, not buried inside. */}
              {c.write_access && c.enabled && <span className="rd-connector-tag warn">Can change DNS</span>}
            </div>
          </div>
          {configurable && <button
            className="rd-btn rd-btn-ghost rd-btn-sm"
            aria-expanded={c.managed ? undefined : open}
            aria-label={`Configure ${c.title}`}
            disabled={busy !== null}
            onClick={() => c.managed
              ? change(c, c.enabled ? "disable" : "enable")
              : open ? setEditing(null) : edit(c)}
          >{c.managed ? (c.enabled ? "Disable" : "Connect") : open ? "Done" : "Configure"}</button>}
        </div>

        {open && <div className="rd-connector-config">
          {c.enabled && <div className="rd-connector-proof">
            <span>{check
              ? (check.ok ? `✓ Verified — ${check.tools} tools available` : `✗ ${check.detail ?? "Verification failed"}`)
              : "Checking…"}</span>
            <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => verify(c)}>
              {busy === c.name ? "Checking…" : "Re-check"}
            </button>
          </div>}

          <div className="rd-connector-desc">{c.description}</div>
          {c.identity && <div className="rd-connector-desc">Connected as {c.identity}</div>}

          {c.managed
            ? <div className="rd-connector-desc">Managed through the remote connector administrator.</div>
            : <>
              {c.credential && <label className="rd-connector-switch">
                <input
                  type="checkbox"
                  aria-label={`${c.title} available to agents`}
                  checked={c.enabled}
                  disabled={busy !== null}
                  onChange={() => c.enabled ? change(c, "disable", false) : change(c, "enable", false)}
                />
                Available to agents
              </label>}
              {c.credential && <div className="rd-connector-desc">
                {c.enabled
                  ? `Turning this off withdraws ${c.title} from the agents below. ${c.stored_secret ? "Your keys stay saved." : "Your CLI login is untouched."}`
                  : `${c.title} is set up but no agent can reach it.`}
              </div>}

              {!c.credential && <div className="rd-connector-desc">
                Finished signing in? <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={refresh}>Refresh credentials</button>
              </div>}

              {c.enabled && (HARNESS_ORDER.some(h => c.harnesses_present?.[h] === false)
                || (c.harnesses ?? HARNESS_ORDER).length < HARNESS_ORDER.length)
                && <div className="rd-connector-desc">{availabilityLabel(c)}</div>}
              <fieldset className="rd-connector-harnesses">
                <legend>Agents</legend>
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

              {c.name === "porkbun" && <>
                <label className="rd-connector-switch">
                  <input type="checkbox" aria-label="Allow changes to domains and DNS records" checked={write} onChange={e => setWrite(e.target.checked)} />
                  Allow changes to domains and DNS records
                </label>
                {write && <div className="rd-connector-warn">
                  Agents can create, edit and <b>delete</b> DNS records on every domain these keys reach.
                </div>}
              </>}

              <label className="rd-connector-source">Credential
                <select aria-label={`${c.title} credential source`} value={source} onChange={e => { setSource(e.target.value); setToken(""); setSecret(""); }}>
                  <option value="" disabled>Choose a source</option>
                  {(c.sources ?? []).map(s => <option key={s} value={s}>{sourceNames[s] ?? s}</option>)}
                </select>
              </label>
              {source === "stored" && <>
                <input aria-label={`${c.title} API key`} type="password" autoComplete="off" value={token} placeholder={c.credential === "stored" ? "Leave blank to reuse stored credentials" : "API key or personal access token"} onChange={e => setToken(e.target.value)} />
                {c.name === "porkbun" && <input aria-label="Porkbun secret key" type="password" autoComplete="off" value={secret} placeholder="Secret key" onChange={e => setSecret(e.target.value)} />}
              </>}
              <div className="rd-connector-desc">{c.stored_secret
                ? "Revocation must be completed on the provider’s website. Disable does not revoke authorization."
                : `Uses your ${c.title} CLI login on this computer. Duckterm stores no credential for it — sign out of that CLI to revoke access.`}</div>

              <div className="rd-connector-installed">
                <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={busy !== null || !source || harnesses.length === 0} onClick={() => change(c, "enable")}>
                  {c.enabled ? "Save" : "Verify and enable"}
                </button>
                {/* Forget deletes a stored secret and disables the connector, so it
                    appears only where Duckterm actually holds the credential. */}
                {c.stored_secret && <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => change(c, "forget")}>Forget stored credentials</button>}
                {c.stored_secret && <a href={c.revoke_url} target="_blank" rel="noreferrer">Revoke at {c.title} ↗</a>}
              </div>
            </>}
        </div>}
      </div>;
    })}
  </div>;
}
