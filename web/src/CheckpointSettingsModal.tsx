import { useEffect, useRef, useState } from "react";
import { api, type CheckpointSettings } from "./api";
import { Button, inputStyle, Modal } from "./ui";
import "./checkpointSettings.css";

const defaults: CheckpointSettings = { lookback_records: "all", writer_model: "haiku", reviewer_model: "sonnet" };

export function CheckpointSettingsModal({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<CheckpointSettings | null>(null);
  const [lookback, setLookback] = useState("all");
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
  const all = lookback === "all";
  const count = Number(lookback);
  const valid = all || (lookback.trim() !== "" && Number.isInteger(count) && count >= 1 && count <= 1000);
  async function save() {
    if (!settings || !valid || sending.current) return;
    sending.current = true; setSaving(true); setSaved(false); setError("");
    try {
      const value = await api.saveCheckpointSettings({ lookback_records: all ? "all" : count, writer_model: settings.writer_model, reviewer_model: settings.reviewer_model });
      setSettings(value); setLookback(String(value.lookback_records)); setSaved(true);
    } catch (e) { setError(`Settings save was not confirmed: ${(e as Error).message}. Reload to check the saved values.`); }
    finally { sending.current = false; setSaving(false); }
  }
  return <Modal title="Checkpointing" onClose={onClose} width={680}>
    <div className="rd-checkpoint-settings">
      <p>Choose what a checkpoint summarizes by default.</p>
      {!settings && !error && <p role="status">Loading checkpoint settings…</p>}
      {settings && <form onSubmit={e => { e.preventDefault(); void save(); }}>
        <fieldset disabled={saving}>
          <label><input type="radio" name="checkpoint-lookback" checked={all} onChange={() => { setLookback("all"); setSaved(false); }} /> All unsummarized messages (default)</label>
          <label><input type="radio" name="checkpoint-lookback" checked={!all} onChange={() => { setLookback("100"); setSaved(false); }} /> Only the latest
            <input id="checkpoint-lookback" aria-label="Latest records per checkpoint" style={inputStyle} type="number" min="1" max="1000" step="1" disabled={all} value={all ? "" : lookback} onChange={e => { setLookback(e.target.value); setSaved(false); }} /></label>
          {!valid && <div role="alert">Enter a whole number from 1 to 1,000.</div>}
          <div className="rd-checkpoint-models">
            {(["writer_model", "reviewer_model"] as const).map(role => <div key={role}>
              <label htmlFor={`checkpoint-${role}`}>{role === "writer_model" ? "Summary writer" : "Pull request reviewer"}</label>
              <select id={`checkpoint-${role}`} style={inputStyle} value={settings[role]} onChange={e => { setSettings({ ...settings, [role]: e.target.value as CheckpointSettings[typeof role] }); setSaved(false); }}>
                <option value="haiku">Claude Haiku</option><option value="sonnet">Claude Sonnet</option><option value="opus">Claude Opus</option>
              </select>
            </div>)}
          </div>
          <footer><span role="status">{saved ? "Settings saved" : saving ? "Saving…" : ""}</span>
            <button type="button" className="rd-btn rd-btn-ghost" onClick={() => { setSettings(defaults); setLookback("all"); setSaved(false); }}>Reset defaults</button>
            <button className="rd-btn rd-btn-primary" type="submit" disabled={!valid || saving}>Save settings</button>
          </footer>
        </fieldset>
      </form>}
      {error && <div role="alert"><p>{error}</p><Button variant="ghost" disabled={saving} onClick={() => { setSettings(null); setSaved(false); setRetry(n => n + 1); }}>Reload settings</Button></div>}
    </div>
  </Modal>;
}
