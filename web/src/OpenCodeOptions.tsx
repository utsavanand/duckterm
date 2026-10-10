import { useEffect, useId, useMemo, useState } from "react";
import { api } from "./api";
import { destinationRequest, desktop } from "./desktop";
import { Button, Field, inputStyle } from "./ui";

export interface OpenCodeLaunchOptions {
  launch: { available: boolean; reason?: string };
  models: { id: string; label: string; configured: boolean }[];
  model_reason?: string;
  capabilities: { id: string; label: string; available: boolean; reason?: string }[];
}

export function validOpenCodeModel(model: string): boolean {
  return model === "" || (new TextEncoder().encode(model).length <= 512
    && /^[A-Za-z0-9_.][A-Za-z0-9_.-]*\/[^\s\x00-\x1f\x7f]+$/u.test(model)); // eslint-disable-line no-control-regex
}

export function openCodeCommand(model: string): string {
  if (!validOpenCodeModel(model)) throw new Error("Use a model ID in provider/model format");
  // The command field is parsed into argv by the destination. Quote the model
  // as one literal argument, including custom IDs containing shell punctuation.
  return model ? "opencode --model '" + model.replaceAll("'", "'\\''") + "'" : "opencode";
}

export function useOpenCodeOptions(enabled: boolean, target: string) {
  const [attempt, retry] = useState(0);
  // A new selection needs a new response even when it returns to the same
  // computer. Keying only by target resurrects an earlier availability result.
  const query = useMemo(() => ({ enabled, target, attempt }), [enabled, target, attempt]);
  const [state, setState] = useState<{ query: typeof query; options?: OpenCodeLaunchOptions; error?: string }>();
  useEffect(() => {
    if (!query.enabled) return;
    let current = true;
    const read = desktop() && query.target !== desktop()?.currentTarget
      ? destinationRequest<OpenCodeLaunchOptions>(query.target, "opencode-options")
      : api.openCodeOptions();
    void read.then(options => {
      if (!options || typeof options.launch?.available !== "boolean"
        || (options.launch.reason !== undefined && typeof options.launch.reason !== "string")
        || (options.model_reason !== undefined && typeof options.model_reason !== "string")
        || !Array.isArray(options.models) || options.models.length > 10000
        || !options.models.every(item => item && typeof item.id === "string" && validOpenCodeModel(item.id) && item.id !== "" && typeof item.label === "string" && typeof item.configured === "boolean")
        || !Array.isArray(options.capabilities) || options.capabilities.length > 32
        || !options.capabilities.every(item => item && typeof item.id === "string" && typeof item.label === "string" && typeof item.available === "boolean" && (item.reason === undefined || typeof item.reason === "string"))) {
        throw new Error("This computer did not report OpenCode support. Update DuckTerm and retry.");
      }
      if (current) setState({ query, options });
    }).catch((error: unknown) => {
      if (current) setState({ query, error: error instanceof Error ? error.message : "Could not check OpenCode support" });
    });
    return () => { current = false; };
  }, [query]);
  const current = enabled && state?.query === query ? state : undefined;
  return { options: current?.options, error: current?.error, retry: () => retry(n => n + 1) };
}

export function OpenCodeModel({ model, onModel, options, disabled }: {
  model: string; onModel: (model: string) => void; options?: OpenCodeLaunchOptions; disabled: boolean;
}) {
  const id = useId();
  return <Field label="Model (optional)">
    <input aria-label="OpenCode model" aria-describedby={id + "-help"} aria-invalid={!validOpenCodeModel(model)}
      style={inputStyle} list={id} value={model} disabled={disabled} onChange={e => onModel(e.target.value)}
      placeholder="Use OpenCode’s configured default" autoComplete="off" spellCheck={false} />
    <datalist id={id}>{options?.models.filter(item => item.configured).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</datalist>
    <small id={id + "-help"} style={{ display: "block", color: "var(--text-soft)", lineHeight: 1.6, marginTop: 6 }}>
      Choose a configured model or enter provider/model. Provider sign-in and configuration stay on the selected computer.
    </small>
    {options?.model_reason && <small style={{ display: "block", color: "var(--text-soft)", marginTop: 6 }}>{options.model_reason}</small>}
    {!validOpenCodeModel(model) && <p role="alert">Use a model ID in provider/model format, without spaces.</p>}
  </Field>;
}

export function OpenCodeSupport({ options, error, retry }: {
  options?: OpenCodeLaunchOptions; error?: string; retry: () => void;
}) {
  return <div style={{ lineHeight: 1.6, margin: "12px 0" }}>
    {!options && !error && <p role="status">Checking OpenCode support on this computer…</p>}
    {error && <div role="alert"><p>{error}</p><Button size="sm" variant="ghost" onClick={retry}>Retry OpenCode check</Button></div>}
    {options && !options.launch.available && <p role="status">{options.launch.reason || "OpenCode launch is unavailable on this computer."}</p>}
    <details style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <summary style={{ cursor: "pointer" }}>OpenCode support</summary>
      <p style={{ color: "var(--text-soft)" }}>Sessions stay in this window alongside your other agents. Model choices use the selected computer’s OpenCode providers.</p>
      {options && <ul style={{ paddingLeft: 20, color: "var(--text-soft)" }}>{options.capabilities.map(item =>
        <li key={item.id} style={{ margin: "8px 0" }}><strong>{item.label}: {item.available ? "Available" : "Unavailable"}.</strong>{item.reason ? " " + item.reason : ""}</li>)}</ul>}
      <p style={{ color: "var(--text-soft)" }}>Questions and approvals use the OpenCode terminal until answering from DuckTerm is supported.</p>
    </details>
  </div>;
}
