import { useEffect, useRef, useState } from "react";
import { api, type CheckpointSettings } from "./api";
import { Button, inputStyle, Modal } from "./ui";
import "./checkpointSettings.css";

const defaults: CheckpointSettings = { lookback_records: 20, batch_records: 20, writer_model: "haiku", reviewer_model: "sonnet" };

export function CheckpointSettingsModal({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<CheckpointSettings | null>(null);
  const [lookback, setLookback] = useState("20");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [retry, setRetry] = useState(0);
  const sending = useRef(false);
  useEffect(() => {
    let canceled = false;
    setError("");
    void api.checkpointSettings().then(value => {
      if (!canceled) { setSettings(value); setLookback(String(value.lookback_records)); }
    }).catch((e: Error) => { if (!canceled) setError(e.message); });
    return () => { canceled = true; };
  }, [retry]);
  const count = Number(lookback);
  const valid = lookback.trim() !== "" && Number.isInteger(count) && count >= 1 && count <= 1000;
  async function save() {
    if (!settings || !valid || sending.current) return;
    sending.current = true; setSaving(true); setSaved(false); setError("");
    try {
      const value = await api.saveCheckpointSettings({ lookback_records: count, writer_model: settings.writer_model, reviewer_model: settings.reviewer_model });
      setSettings(value); setLookback(String(value.lookback_records)); setSaved(true);
    } catch (e) { setError(`Settings save was not confirmed: ${(e as Error).message}. Reload to check the saved values.`); }
    finally { sending.current = false; setSaving(false); }
  }
  return <Modal title="Checkpointing" onClose={onClose} width={680}>
    <div className="rd-checkpoint-settings">
      <p>Choose the default count offered when you create a checkpoint.</p>
      {!settings && !error && <p role="status">Loading checkpoint settings…</p>}
      {settings && <form onSubmit={e => { e.preventDefault(); void save(); }}>
        <fieldset disabled={saving}>
          <label htmlFor="checkpoint-lookback">Default records per checkpoint</label>
          <input id="checkpoint-lookback" style={inputStyle} type="number" min="1" max="1000" step="1" required value={lookback} onChange={e => { setLookback(e.target.value); setSaved(false); }} aria-describedby="checkpoint-record-help" />
          <small id="checkpoint-record-help">Default: 20 records. A record is a message or message segment. A conversational turn can contain several records.</small>
          <div className="rd-checkpoint-batch-note" aria-live="polite">{valid ? <>
            <strong>Up to {count} unprocessed records, in batches of up to 20.</strong><br />
            At least {Math.ceil(count / 20)} {count > 20 ? "batches" : "batch"} if this many records are selected. Large records may require smaller batches; saved work is reused.
          </> : "Enter a whole number from 1 to 1,000."}</div>
          <div className="rd-checkpoint-models">
            {(["writer_model", "reviewer_model"] as const).map(role => <div key={role}>
              <label htmlFor={`checkpoint-${role}`}>Summary {role === "writer_model" ? "writer" : "reviewer"}</label>
              <select id={`checkpoint-${role}`} style={inputStyle} value={settings[role]} onChange={e => { setSettings({ ...settings, [role]: e.target.value as CheckpointSettings[typeof role] }); setSaved(false); }}>
                <option value="haiku">Claude Haiku</option><option value="sonnet">Claude Sonnet</option><option value="opus">Claude Opus</option>
              </select>
              <small>{role === "writer_model" ? "Writes the summary and extracts decisions, constraints, and open items." : "Checks the result against the captured source records."}</small>
            </div>)}
          </div>
          <p className="rd-checkpoint-settings-note">The checkpoint dialog shows how many records remain and lets you change the count once. It processes oldest unprocessed records first. Previously saved decisions and open items remain in memory.</p>
          <small>Model choices use your installed Claude Code model aliases. A custom summary provider takes precedence.</small>
          <footer><span role="status">{saved ? "Settings saved" : saving ? "Saving…" : ""}</span>
            <button type="button" className="rd-btn rd-btn-ghost" onClick={() => { setSettings(defaults); setLookback("20"); setSaved(false); }}>Reset defaults</button>
            <button className="rd-btn rd-btn-primary" type="submit" disabled={!valid || saving}>Save settings</button>
          </footer>
        </fieldset>
      </form>}
      {error && <div role="alert"><p>{error}</p><Button variant="ghost" disabled={saving} onClick={() => { setSettings(null); setSaved(false); setRetry(n => n + 1); }}>Reload settings</Button></div>}
      <small className="rd-checkpoint-settings-scope">Applies to future manual checkpoints on this DuckTerm instance. Harness-switch preparation uses its own range.</small>
    </div>
  </Modal>;
}
