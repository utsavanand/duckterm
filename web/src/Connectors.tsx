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
      const editingThis = editing === c.name;
      // One badge, one fixed place. Connection state was spread across a dot,
      // a word and two door lines; read in one spot it needs none of that.
      const badge = !connected ? { cls: "off", text: "Off" }
        : lost.length > 0 ? { cls: "warn", text: "Needs attention" }
        : c.agents_use ? { cls: "on", text: "Connected" }
        : { cls: "warn", text: "Duckterm only" };
      // A card's footer only exists if something would sit in it.
      const hasFooter = connected && !c.managed && !editingThis;
      return <div key={c.name} className={`rd-card${hasFooter ? " has-footer" : ""}`}>
        <div className="rd-card-head">
          <div className="rd-card-title">{c.title}</div>
          <div className="rd-card-desc">
            {connected
              ? <>{c.identity && <span className="rd-card-identity">{c.identity}</span>}
                  {c.identity && " · "}{sourceNames[c.credential!] ?? c.credential}</>
              : "Not connected"}
          </div>
          <div className="rd-card-action">
            <span className={`rd-badge rd-badge-${badge.cls}`}>
              {badge.cls !== "off" && <span className="rd-badge-dot" aria-hidden="true" />}
              {badge.text}
            </span>
          </div>
        </div>

        <div className="rd-card-body">
          {c.managed ? <div className="rd-connector-desc">Managed through the remote connector administrator.</div> : <>
            {!connected && !editingThis && <>
              {c.runner_ready === false && <div className="rd-alert">Before connecting: {c.detail}</div>}
              <div className="rd-connector-actions">
                {(c.sources ?? []).includes("gh-cli") && <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={busy !== null} onClick={() => { setSource("gh-cli"); setToken(""); setHarnesses(c.harnesses ?? HARNESS_ORDER); void change({ ...c, credential: "gh-cli" }, "enable", c.harnesses ?? HARNESS_ORDER); }}>
                  Connect
                </button>}
                <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy !== null} onClick={() => { setEditing(c.name); setSource("stored"); setToken(""); setHarnesses(c.harnesses ?? HARNESS_ORDER); }}>
                  Paste a token
                </button>
              </div>
            </>}

            {connected && !editingThis && <>
              {lost.length > 0 && !c.agents_off_by_choice && <div className="rd-alert">
                {`${harnessList(lost)} ${lost.length > 1 ? "have" : "has"} no entry for it. Re-register below, or deselect ${lost.length > 1 ? "them" : "it"}.`}
              </div>}
              {c.runner_ready === false && <div className="rd-alert">Agents also need the server to run: {c.detail}</div>}

              {/* The agents door is a state you leave on, not an action you
                  fire, so it is a switch. A button whose label flipped made
                  you read the label to learn the state. Duckterm's own door
                  needs no control: holding a credential is using it. */}
              <div className="rd-field">
                <div className="rd-field-content">
                  <label className="rd-field-label" htmlFor={`share-${c.name}`}>Share with agents</label>
                  <div className="rd-field-desc">
                    {c.agents_use
                      ? `${agentReachLabel(c)} can use it.`
                      : c.agents_off_by_choice
                        ? "Off. Duckterm still uses this credential."
                        : `On, but ${harnessList(lost)} cannot reach it.`}
                  </div>
                </div>
                <button
                  id={`share-${c.name}`}
                  role="switch"
                  type="button"
                  aria-checked={!c.agents_off_by_choice}
                  aria-label={`Share ${c.title} with agents`}
                  className="rd-switch"
                  disabled={busy !== null}
                  onClick={() => change(c, c.agents_off_by_choice ? "enable" : "disable", c.agents_off_by_choice ? c.harnesses ?? HARNESS_ORDER : undefined)}
                />
              </div>

              {c.agents_use && <div className="rd-field">
                <div className="rd-field-content">
                  <div className="rd-field-label">Verified</div>
                  <div className="rd-field-desc">{checks[c.name]
                    ? (checks[c.name].ok
                        ? `✓ ${checks[c.name].tools} tools`
                        : `✗ ${checks[c.name].detail ?? "Verification failed"}`)
                    : "Not verified"} · {lastUsedLabel(c)}</div>
                </div>
                <button className="rd-btn rd-btn-sm" disabled={busy !== null} onClick={() => verify(c)}>
                  {busy === c.name ? "Checking…" : "Check now"}
                </button>
              </div>}
            </>}

            {editingThis && <div className="rd-connector-token">
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
              <div className="rd-connector-actions">
                <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={busy !== null || !source || harnesses.length === 0} onClick={() => change(c, "enable")}>
                  {harnesses.length ? "Verify and share" : "Verify"}
                </button>
                {/* "Cancel" is the clearer word beside "Verify and share", but
                    the accessible name keeps the connector in it: the panel can
                    list more than one editor-bearing card. */}
                <button className="rd-btn rd-btn-ghost rd-btn-sm" aria-label={`Close ${c.title} connection settings`} onClick={() => { setEditing(null); setToken(""); }}>Cancel</button>
              </div>
            </div>}
          </>}
        </div>

        {/* Card-level actions have one home. Sign out is pushed to the far
            end so a routine control is never adjacent to the destructive one. */}
        {hasFooter && <div className="rd-card-foot">
          <button className="rd-btn rd-btn-sm" disabled={busy !== null} onClick={() => edit(c)}>Change credential</button>
          <a className="rd-linkbtn" href={c.revoke_url} target="_blank" rel="noreferrer">Revoke at github.com ↗</a>
          <span className="rd-card-foot-spacer" />
          <button className="rd-btn rd-btn-danger rd-btn-sm" disabled={busy !== null} onClick={() => change(c, "forget")}>Sign out</button>
        </div>}
      </div>;
    })}
  </div>;
}
