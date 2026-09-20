import { useEffect, useState } from "react";
import { api, Connector } from "./api";
import { useToast } from "./ui";

// Bottom half of the right column: machine-global connectors. Enabling one
// stores/uses a credential (preferring an existing CLI login) and registers
// the service's MCP server in the claude-code and codex user configs — the
// backend keeps tokens out of those plaintext files via a launch-time shim.
export function Connectors() {
  const toast = useToast();
  const [rows, setRows] = useState<Connector[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  // Credential fallback inputs: GitHub shows one (PAT) when no gh login
  // exists; Porkbun always needs two (API key + secret key).
  const [tokenFor, setTokenFor] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");

  const refresh = () =>
    api
      .connectors()
      .then((d) => setRows(d.connectors))
      .catch(() => undefined);
  useEffect(() => {
    refresh();
  }, []);

  async function toggle(c: Connector) {
    const needsKeys =
      !c.enabled &&
      !c.credential &&
      ((c.name === "github" && !token.trim()) ||
        (c.name === "porkbun" && !(token.trim() && secret.trim())));
    if (needsKeys) {
      setTokenFor(c.name); // reveal the credential input(s) instead of failing
      return;
    }
    setBusy(c.name);
    try {
      const next = c.enabled
        ? await api.disableConnector(c.name)
        : await api.enableConnector(
            c.name,
            token.trim() || undefined,
            secret.trim() || undefined,
          );
      setRows((rs) => rs.map((r) => (r.name === next.name ? next : r)));
      setTokenFor(null);
      setToken("");
      setSecret("");
      toast(
        next.enabled
          ? `${next.title} connected — takes effect on NEW sessions`
          : `${next.title} disconnected`,
      );
    } catch (e) {
      toast(`${c.title}: ${(e as Error).message}`, "err");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rd-connectors">
      <div className="rd-panel-head">
        <span>Connectors</span>
      </div>
      {rows.map((c) => (
        <div key={c.name} className="rd-connector">
          <div className="rd-connector-row">
            <span className={`dot ${c.enabled ? "on" : c.ready ? "off" : "warn"}`} />
            <span className="rd-connector-title">{c.title}</span>
            <span className="rd-connector-cred">
              {c.credential ? `via ${c.credential}` : (c.detail ?? "")}
            </span>
            <button
              className="rd-btn rd-btn-ghost rd-btn-sm"
              // github/porkbun stay clickable when not ready: the click reveals
              // the key inputs; others (CLI-login based) have nothing to type.
              disabled={
                busy === c.name ||
                (!c.enabled &&
                  !c.ready &&
                  c.name !== "github" &&
                  c.name !== "porkbun")
              }
              onClick={() => toggle(c)}
            >
              {busy === c.name ? "…" : c.enabled ? "Disconnect" : "Connect"}
            </button>
          </div>
          <div className="rd-connector-desc">{c.description}</div>
          {c.enabled && (
            <div className="rd-connector-installed">
              {Object.entries(c.installed)
                .map(([h, ok]) => `${h}: ${ok ? "✓" : "✗"}`)
                .join("  ·  ")}
            </div>
          )}
          {tokenFor === c.name && (
            <div className="rd-connector-token">
              <input
                autoFocus
                type="password"
                value={token}
                placeholder={
                  c.name === "porkbun"
                    ? "API key (pk1_…)"
                    : "GitHub personal access token"
                }
                onChange={(e) => setToken(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") toggle(c);
                  if (e.key === "Escape") setTokenFor(null);
                }}
              />
              {c.name === "porkbun" && (
                <input
                  type="password"
                  value={secret}
                  placeholder="Secret key (sk1_…)"
                  onChange={(e) => setSecret(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") toggle(c);
                    if (e.key === "Escape") setTokenFor(null);
                  }}
                />
              )}
              <button
                className="rd-btn rd-btn-sm rd-btn-primary"
                disabled={
                  busy === c.name ||
                  !token.trim() ||
                  (c.name === "porkbun" && !secret.trim())
                }
                onClick={() => toggle(c)}
              >
                Save
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
