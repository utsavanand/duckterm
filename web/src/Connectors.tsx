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

function harnessList(names: string[]): string {
  const labels = names.map(h => harnessNames[h] ?? h);
  if (labels.length <= 1) return labels[0] ?? "";
  return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}

// What agents can reach it, naming the ones whose CLI is absent: a registration
// is written whether or not the agent exists here, so "registered" is not
// "usable on this computer".
function agentReachLabel(c: Connector): string {
  const chosen = c.harnesses ?? HARNESS_ORDER;
  const named = chosen.map(h => {
    const label = harnessNames[h] ?? h;
    return c.harnesses_present?.[h] === false ? `${label} (not installed here)` : label;
  });
  // "through the GitHub MCP server" is how it works, not whether it works.
  return named.join(" and ");
}

const sourceNames: Record<string, string> = {
  "gh-cli": "GitHub CLI login",
  stored: "Stored personal access token",
};

export function Connectors({ sessionKey = "" }: { sessionKey?: string }) {
  const toast = useToast();
  const panel = useRef<HTMLDivElement>(null);
  const [rows, setRows] = useState<Connector[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [token, setToken] = useState("");
  const [harnesses, setHarnesses] = useState<string[]>(HARNESS_ORDER);
  const [checks, setChecks] = useState<Record<string, ConnectorCheck & { at: number }>>({});
  // Status and usage belong to a host, not its selected session. In-flight CLI
  // probes must not multiply every time the selection or window focus changes.
  const host = splitSessionRef(sessionKey).host;
  const context = host === "local" ? "" : sessionRef(host, "connectors");
  const currentHost = useRef(host);
  currentHost.current = host;
  const pending = useRef<AbortController | null>(null);
  const revision = useRef(0);
  const lifetime = useRef(0);
  const invalidate = useCallback(() => {
    ++revision.current;
    pending.current?.abort();
    pending.current = null;
  }, []);

  async function verify(c: Connector) {
    const epoch = lifetime.current;
    const current = () => lifetime.current === epoch && currentHost.current === host;
    setBusy(c.name);
    try {
      const result = await api.verifyConnector(c.name, sessionKey);
      if (!current()) return;
      setChecks(prev => ({ ...prev, [c.name]: { ...result, at: Date.now() } }));
    } catch (e) {
      if (current()) toast(`${c.title}: ${(e as Error).message}`, "err");
    } finally {
      if (current()) setBusy(null);
    }
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
    setRows([]); setChecks({}); setEditing(null); setToken(""); setBusy(null);
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

  function edit(c: Connector) {
    setEditing(c.name);
    setSource(c.credential ?? (c.sources?.includes("gh-cli") ? "gh-cli" : "stored"));
    setHarnesses(c.harnesses ?? HARNESS_ORDER);
    setToken("");
  }

  async function change(c: Connector, action: "enable" | "disable" | "forget", chosen?: string[]) {
    const epoch = lifetime.current;
    const current = () => lifetime.current === epoch && currentHost.current === host;
    if (action === "forget" && !window.confirm(`Sign out of ${c.title} and delete the token Duckterm stored? Your GitHub CLI login and the authorization on github.com are untouched.`)) return;
    setBusy(c.name);
    try {
      const next = action === "enable"
        ? await api.enableConnector(c.name, editing === c.name ? token.trim() || undefined : undefined, editing === c.name ? source : c.credential ?? undefined, sessionKey, chosen ?? (editing === c.name ? harnesses : undefined))
        : action === "forget" ? await api.forgetConnector(c.name, sessionKey) : await api.disableConnector(c.name, sessionKey);
      if (!current()) return;
      invalidate(); // An older status response cannot undo this confirmed change.
      setRows(rs => rs.map(r => r.name === next.name ? next : r));
      setEditing(null);
      setToken("");
      // Name the door that moved. "Disabled" was read as Duckterm dropping the
      // credential, which is what Sign out does, not what this does.
      toast(action === "enable"
        ? `${c.title} shared with ${harnessList(next.harnesses ?? HARNESS_ORDER)}`
        : action === "forget" ? `Signed out of ${c.title} — stored token deleted`
        : `${c.title} withdrawn from agents — Duckterm still uses it`);
    } catch (e) {
      if (!current()) return;
      setToken(""); // Clear submitted secrets even when validation fails.
      toast(`${c.title}: ${(e as Error).message}`, "err");
    } finally {
      if (current()) setBusy(null);
    }
  }

  return <div ref={panel} className="rd-connectors">
    <div className="rd-panel-head"><span>Connectors · {hostName(sessionKey)}</span><button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={refresh}>Refresh</button></div>
    {rows.map(c => {
      const connected = !!c.credential;
      const lost = c.unreachable_harnesses ?? [];
      return <div key={c.name} className="rd-connector">
        <div className="rd-connector-row">
          <span className={`dot ${c.agents_use ? "on" : connected ? "part" : "off"}`} />
          <span className="rd-connector-title">{c.title}</span>
          {/* An identity is a value and stays monospace; "Not connected" is a
              state, and monospace made it read as returned output. */}
          <span className={`rd-connector-cred${connected ? "" : " rd-connector-state"}`}>
            {connected
              ? c.identity ?? "Identity not verified — reconnect to verify"
              : "Not connected"}
          </span>
          {connected && !c.managed && <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => edit(c)}>Change</button>}
          {editing === c.name && <button className="rd-btn rd-btn-ghost rd-btn-sm" aria-label={`Close ${c.title} connection settings`} onClick={() => { setEditing(null); setToken(""); }}>Close</button>}
        </div>

        {/* Which credential is in use is worth saying; what GitHub is is not. */}
        {connected && <div className="rd-connector-desc">{sourceNames[c.credential!] ?? c.credential}</div>}

        {/* The two doors still report separately -- one line for both read as
            "Duckterm stopped using it" whenever agents were off. But each is a
            state, so each is one line. The paragraph under "Duckterm uses it"
            listed features that were never in question; the paragraph under
            the agents line only earns its place when something is wrong. */}
        {connected && <div className="rd-connector-doors">
          <div className="rd-door on">
            <span className="rd-door-mark" aria-hidden="true">▸</span>
            <div><strong>Duckterm uses it</strong></div>
          </div>
          <div className={`rd-door ${c.agents_use ? "on" : "off"}`}>
            <span className="rd-door-mark" aria-hidden="true">{c.agents_use ? "▸" : "×"}</span>
            <div>
              <strong>{c.agents_use ? `Agents use it — ${agentReachLabel(c)}` : "Agents cannot use it"}</strong>
              {!c.agents_use && lost.length > 0 && !c.agents_off_by_choice && <div className="rd-connector-desc">
                {`${harnessList(lost)} ${lost.length > 1 ? "have" : "has"} no entry for it. Re-register below, or deselect ${lost.length > 1 ? "them" : "it"}.`}
              </div>}
            </div>
          </div>
        </div>}

        {connected && c.runner_ready === false && <div className="rd-connector-desc rd-connector-warn">
          Agents also need the server to run: {c.detail}
        </div>}

        {/* One span, not two: as separate flex items the status and the usage
            could not share a line with the button at panel width. "Configured"
            added nothing -- the row above already says agents use it. */}
        {c.agents_use && <div className="rd-connector-desc rd-connector-proof">
          <span>{checks[c.name]
            ? (checks[c.name].ok
                ? `✓ ${checks[c.name].tools} tools`
                : `✗ ${checks[c.name].detail ?? "Verification failed"}`)
            : "Not verified"} · {lastUsedLabel(c)}</span>
          <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => verify(c)}>
            {busy === c.name ? "Checking…" : "Check now"}
          </button>
        </div>}

        {c.managed ? <div className="rd-connector-desc">Managed through the remote connector administrator.</div> : <>
          {/* Disconnected is a button, not a briefing. What GitHub is needs no
              explaining, and a passing prerequisite check is not news -- only
              its failure is. Both were prose the owner had to read past to
              reach the one control that matters. */}
          {!connected && editing !== c.name && <div className="rd-connector-connect">
            {c.runner_ready === false && <div className="rd-connector-desc rd-connector-warn">Before connecting: {c.detail}</div>}
            <div className="rd-connector-actions">
              {(c.sources ?? []).includes("gh-cli") && <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={busy !== null} onClick={() => { setSource("gh-cli"); setToken(""); setHarnesses(c.harnesses ?? HARNESS_ORDER); void change({ ...c, credential: "gh-cli" }, "enable", c.harnesses ?? HARNESS_ORDER); }}>
                Connect
              </button>}
              <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => { setEditing(c.name); setSource("stored"); setToken(""); setHarnesses(c.harnesses ?? HARNESS_ORDER); }}>
                Paste a token
              </button>
            </div>
          </div>}

          {connected && <div className="rd-connector-installed">
            <div className="rd-connector-actions">
              {c.agents_use
                ? <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => change(c, "disable")}>Stop sharing with agents</button>
                : <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={busy !== null} onClick={() => change(c, "enable", c.harnesses ?? HARNESS_ORDER)}>Share with agents</button>}
              <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => change(c, "forget")}>Sign out</button>
            </div>
            {/* What Sign out does is already in its confirm dialog, which is
                where it matters. Here one link is enough. */}
            <div>Revoking happens <a href={c.revoke_url} target="_blank" rel="noreferrer">on github.com ↗</a></div>
          </div>}

          {editing === c.name && <div className="rd-connector-token">
            {/* Label above the control, as every other form in the app does.
                Wrapping the select inside the label put the two on one line
                with no gap, and the select kept the browser's own font. */}
            <div className="rd-connector-field">
              {/* The visible word is "Credential"; the accessible name keeps
                  the connector in it, since the panel can list more than one. */}
              <label htmlFor={`cred-${c.name}`}>
                <span aria-hidden="true">Credential</span>
                <span className="rd-sr-only">{c.title} credential source</span>
              </label>
              <select id={`cred-${c.name}`} value={source} onChange={e => { setSource(e.target.value); setToken(""); }}>
                {(c.sources ?? []).map(s => <option key={s} value={s}>{sourceNames[s] ?? s}</option>)}
              </select>
            </div>
            {source === "stored" && <>
              <input aria-label={`${c.title} personal access token`} type="password" autoComplete="off" value={token} placeholder={c.credential === "stored" ? "Leave blank to reuse the stored token" : "ghp_… or github_pat_…"} onChange={e => setToken(e.target.value)} />
              {/* The one note worth keeping: a gh token cannot go below
                  repo + read:org + gist, so a fine-grained token is the only
                  way to hand agents less, and you can only act on that here. */}
              <div className="rd-connector-desc">A fine-grained token can be read-only, or limited to some repositories. Your CLI login cannot.</div>
            </>}
            <fieldset className="rd-connector-harnesses">
              <legend>Share with</legend>
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
            <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={busy !== null || !source || harnesses.length === 0} onClick={() => change(c, "enable")}>
              {harnesses.length ? "Verify and share" : "Verify"}
            </button>
          </div>}
        </>}
      </div>;
    })}
  </div>;
}
