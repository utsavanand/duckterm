import { useEffect, useRef, useState } from "react";
import { api, type AiProvider, type AiSettings, type AiState, type CheckpointSettings } from "./api";
import { Button, inputStyle, Modal } from "./ui";
import "./aiModels.css";

const PROVIDERS: { id: AiProvider; label: string; env: string }[] = [
  { id: "anthropic", label: "Anthropic", env: "ANTHROPIC_API_KEY" },
  { id: "openai", label: "OpenAI", env: "OPENAI_API_KEY" },
  { id: "openrouter", label: "OpenRouter", env: "OPENROUTER_API_KEY" },
  { id: "custom", label: "Custom (OpenAI-compatible URL)", env: "" },
];
const FAST_USE = "Checkpoint writer, rule from English, compaction, release map";
const DEEP_USE = "Oracle, checkpoint reviewer, PR review, rule suggestions";
const HARNESS_NAME = { "claude-code": "Claude Code", codex: "Codex" } as const;

export function AiModelsModal({ onClose }: { onClose: () => void }) {
  const [state, setState] = useState<AiState | null>(null);
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [checkpoint, setCheckpoint] = useState<CheckpointSettings | null>(null);
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [keyStatus, setKeyStatus] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const sending = useRef(false);

  useEffect(() => {
    let canceled = false;
    setError("");
    void Promise.all([api.aiSettings(), api.checkpointSettings()]).then(([ai, chosen]) => {
      if (canceled) return;
      setState(ai); setSettings(ai.settings); setCheckpoint(chosen);
    }).catch((e: Error) => { if (!canceled) setError(e.message); });
    return () => { canceled = true; };
  }, [retry]);

  async function run(work: () => Promise<void>) {
    if (sending.current) return;
    sending.current = true; setBusy(true); setError("");
    try { await work(); } catch (e) { setError((e as Error).message); } finally { sending.current = false; setBusy(false); }
  }

  function change(patch: Partial<AiSettings>) {
    if (!settings) return;
    // A base URL belongs to the custom provider only; carrying it into another
    // provider would be refused by the server.
    setSettings({ ...settings, ...patch, ...(patch.provider ? { base_url: "" } : {}) }); setSaved(false);
    if (patch.provider) { setModels([]); setKeyStatus(""); setKey(""); }
  }

  const verify = () => run(async () => {
    if (!settings) return;
    const result = await api.saveAiKey(settings.provider, key.trim(), settings.base_url);
    setKey(""); setModels(result.models);
    setKeyStatus(`Saved in your Keychain · ${result.models.length} ${result.models.length === 1 ? "model" : "models"} available`);
    setState(await api.aiSettings());
  });

  const forget = () => run(async () => {
    if (!settings) return;
    await api.forgetAiKey(settings.provider);
    setModels([]); setKeyStatus("Key removed from your Keychain");
    setState(await api.aiSettings());
  });

  const save = () => run(async () => {
    if (!settings || !checkpoint) return;
    setSaved(false);
    const next = await api.saveAiSettings(settings);
    if (settings.backend === "harness") {
      setCheckpoint(await api.saveCheckpointSettings({
        lookback_records: checkpoint.lookback_records,
        writer_model: checkpoint.writer_model,
        reviewer_model: checkpoint.reviewer_model,
      }));
    }
    setState(next); setSettings(next.settings); setSaved(true);
  });

  const harness = state?.harness ? HARNESS_NAME[state.harness] : null;
  const provider = PROVIDERS.find(p => p.id === settings?.provider) ?? PROVIDERS[0];
  const keyInfo = state && settings ? state.keys[settings.provider] : null;

  return <Modal title="AI models" onClose={onClose} width={640}>
    <div className="rd-ai-models">
      <p>Oracle, checkpoints, PR review and rule suggestions use these. Your coding sessions always run on their own harness login.</p>
      {!settings && !error && <p role="status">Loading AI settings…</p>}
      {settings && checkpoint && state && <form onSubmit={e => { e.preventDefault(); void save(); }}>
        <fieldset disabled={busy}>
          <legend>Run DuckTerm's AI on</legend>
          <label className={`rd-ai-option${settings.backend === "harness" ? " is-on" : ""}`}>
            <input type="radio" name="ai-backend" checked={settings.backend === "harness"} onChange={() => change({ backend: "harness" })} />
            <span><b>Your {harness ?? "Claude Code"} login {harness ? <span className="rd-ai-chip is-ok">detected</span> : <span className="rd-ai-chip">not installed</span>}</b>
              <small>Free on your subscription. Counts toward its usage limit.</small></span>
          </label>
          <label className={`rd-ai-option${settings.backend === "api" ? " is-on" : ""}`}>
            <input type="radio" name="ai-backend" checked={settings.backend === "api"} onChange={() => change({ backend: "api" })} />
            <span><b>Your API key</b>
              <small>Anthropic, OpenAI, OpenRouter or any OpenAI-compatible server. About 1 s faster per call, billed per token by your provider.</small></span>
          </label>
          <label className="rd-ai-option is-off">
            <input type="radio" name="ai-backend" disabled checked={settings.backend === "duckterm"} />
            <span><b>DuckTerm models <span className="rd-ai-chip">coming soon</span></b>
              <small>Pay with DuckTerm credits; no key needed.</small></span>
          </label>

          {settings.backend !== "api" && <>
            <label htmlFor="ai-harness">Harness</label>
            <select id="ai-harness" style={inputStyle} value={settings.harness} onChange={e => change({ harness: e.target.value as AiSettings["harness"] })}>
              <option value="auto">Automatic (Claude Code, then Codex)</option>
              <option value="claude-code">Claude Code</option>
              <option value="codex">Codex</option>
            </select>
            <div className="rd-ai-tiers">
              {(["writer_model", "reviewer_model"] as const).map(role => <div key={role}>
                <label htmlFor={`ai-${role}`}>{role === "writer_model" ? "Fast model" : "Deep model"}</label>
                <select id={`ai-${role}`} style={inputStyle} value={checkpoint[role]} onChange={e => { setCheckpoint({ ...checkpoint, [role]: e.target.value as CheckpointSettings[typeof role] }); setSaved(false); }}>
                  <option value="haiku">Haiku</option><option value="sonnet">Sonnet</option><option value="opus">Opus</option>
                </select>
                <small>{role === "writer_model" ? FAST_USE : DEEP_USE}</small>
              </div>)}
            </div>
            <small>Model choices apply when Claude Code serves the call. Codex uses GPT-6-Luna for fast calls when your account offers it, and its default model otherwise.</small>
          </>}

          {settings.backend === "api" && <>
            <label htmlFor="ai-provider">Provider</label>
            <select id="ai-provider" style={inputStyle} value={settings.provider} onChange={e => change({ provider: e.target.value as AiProvider })}>
              {PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
            {settings.provider === "custom" && <>
              <label htmlFor="ai-base-url">Base URL</label>
              <input id="ai-base-url" style={inputStyle} type="url" placeholder="http://localhost:11434/v1" value={settings.base_url} onChange={e => change({ base_url: e.target.value })} />
            </>}
            <label htmlFor="ai-key">API key</label>
            {keyInfo?.source === "env"
              ? <small>Using {provider.env} from DuckTerm's environment.</small>
              : <div className="rd-ai-key">
                <input id="ai-key" style={inputStyle} type="password" autoComplete="off" placeholder={keyInfo?.saved ? "Saved — paste a new key to replace it" : "Paste your key"} value={key} onChange={e => setKey(e.target.value)} />
                <button type="button" className="rd-btn" disabled={!key.trim()} onClick={() => void verify()}>Verify and save</button>
              </div>}
            <div role="status" className="rd-ai-key-status">{keyStatus || (keyInfo?.saved && keyInfo.source === "stored" ? "Saved in your Keychain" : "")}</div>
            <datalist id="ai-models">{models.map(m => <option key={m} value={m} />)}</datalist>
            <div className="rd-ai-tiers">
              {(["fast_model", "deep_model"] as const).map(tier => <div key={tier}>
                <label htmlFor={`ai-${tier}`}>{tier === "fast_model" ? "Fast model" : "Deep model"}</label>
                <input id={`ai-${tier}`} style={inputStyle} list="ai-models" value={settings[tier]} placeholder={state.models[tier === "fast_model" ? "fast" : "deep"] || "model id"} onChange={e => change({ [tier]: e.target.value })} />
                <small>{tier === "fast_model" ? FAST_USE : DEEP_USE}</small>
              </div>)}
            </div>
            <div className="rd-ai-box">If the key fails (expired, rate-limited, provider down), the call runs on your {harness ?? "harness"} login instead. DuckTerm never switches to anything that bills you without your setting it here.</div>
            <div className="rd-ai-box is-warn">PR reviews and Oracle questions send their text to {provider.label === PROVIDERS[3].label ? "your server" : provider.label} under this key. A PR review is about 9 calls of up to 20k tokens each.</div>
          </>}

          <footer><span role="status">{saved ? "Settings saved" : busy ? "Working…" : ""}</span>
            {settings.backend === "api" && keyInfo?.saved && keyInfo.source === "stored" && <button type="button" className="rd-btn rd-btn-ghost" onClick={() => void forget()}>Forget key</button>}
            <button type="button" className="rd-btn rd-btn-ghost" onClick={onClose}>Cancel</button>
            <button className="rd-btn rd-btn-primary" type="submit">Save</button>
          </footer>
        </fieldset>
      </form>}
      {error && <div role="alert"><p>{error}</p>{!settings && <Button variant="ghost" onClick={() => setRetry(n => n + 1)}>Reload settings</Button>}</div>}
    </div>
  </Modal>;
}
