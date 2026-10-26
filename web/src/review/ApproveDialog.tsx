import { useEffect, useRef, useState } from "react";
import { api, type ApprovalRecord, type ApprovalState } from "../api";
import { approvalSigner } from "../desktop";
import { Modal } from "../ui";
import { shortSha } from "./diffModel";

export function approvalLabel(record: ApprovalRecord): string {
  const task = record.task;
  if (!task) return `Approved · handed to ${record.release.name}`;
  if (task.status === "parked") return `Release stopped: ${task.note || "no reason given"}`;
  if (task.status === "done") return task.note ? `Released · ${task.note}` : "Released";
  return `Approved · ${record.release.name}: ${task.note || "starting"}`;
}

/** Approve = merge and release, confirmed with Touch ID in the Mac app. The
 *  server re-checks every condition and the signature; this only shows them. */
export function ApproveDialog({ repo, target, prNumber, onClose, onApproved }: {
  repo: string; target: string; prNumber: number; onClose: () => void; onApproved: () => void;
}) {
  const [state, setState] = useState<ApprovalState | null>(null);
  const [bound, setBound] = useState<"checking" | "bound" | "unavailable" | "refused">("checking");
  const [bindError, setBindError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [record, setRecord] = useState<ApprovalRecord | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  useEffect(() => {
    void (async () => {
      try {
        const loaded = await api.approvalState(repo, target);
        if (alive.current) { setState(loaded); setRecord(loaded.record); }
      } catch (cause) { if (alive.current) setError((cause as Error).message); }
      const signer = approvalSigner();
      if (!signer) { if (alive.current) setBound("unavailable"); return; }
      try {
        await api.bindApprovalKey(await signer.publicKey());
        if (alive.current) setBound("bound");
      } catch (cause) { if (alive.current) { setBound("refused"); setBindError((cause as Error).message); } }
    })();
  }, [repo, target]);

  async function approve() {
    const signer = approvalSigner();
    if (!signer) return;
    setBusy(true); setError("");
    try {
      const { payload } = await api.approvalChallenge(repo, target);
      const signature = await signer.sign(payload);
      const result = await api.approvalSubmit(repo, target, { payload, signature });
      if (alive.current) setRecord(result);
      onApproved();
    } catch (cause) { if (alive.current) setError((cause as Error).message); }
    finally { if (alive.current) setBusy(false); }
  }

  const current = record && state && record.head_sha === state.head_sha ? record : null;
  const canApprove = !!state?.ready && bound === "bound" && !current && !busy;
  return <Modal title={`Approve #${prNumber}: merge and release`} onClose={onClose}>
    <div className="rd-approve">
      <p className="rd-card-meta">
        Your Touch ID approves this exact head{state ? <> (<code>{shortSha(state.head_sha)}</code>)</> : ""}.
        {state?.release ? ` ${state.release.name} then merges only that commit, bumps the version, tags, builds, releases and installs, and reports each step here.` : ""}
        {" "}A push after you approve cancels it.
      </p>
      {!state && !error && <p className="rd-prs-empty" role="status">Checking…</p>}
      {state && <ul className="rd-approve-checks" aria-label="Approval conditions">
        {state.checks.map(check => <li key={check.name} className={check.ok ? "is-ok" : "is-fail"}>
          <span aria-hidden="true">{check.ok ? "✓" : "✕"}</span> <strong>{check.name}</strong> <small>{check.note}</small>
        </li>)}
      </ul>}
      {state && state.flags.length > 0 && <div className="rd-approve-flags" role="note" aria-label="Release touches">
        <strong>This release touches:</strong>
        <ul>{state.flags.map(flag => <li key={flag.name}>{flag.name}: <code>{flag.hits.join(", ")}</code></li>)}</ul>
      </div>}
      {current && <p className="rd-prs-note" role="status">{approvalLabel(current)}</p>}
      {bound === "unavailable" && <p className="rd-prs-note">Approve with Touch ID works in the DuckTerm Mac app on this computer.</p>}
      {bound === "refused" && <p className="rd-prs-note" role="alert">{bindError}</p>}
      {error && <p className="rd-prs-note" role="alert">{error}</p>}
      <div className="rd-review-dialog-actions">
        <button type="button" className="rd-btn rd-btn-ghost" onClick={onClose}>{current ? "Close" : "Cancel"}</button>
        {!current && <button type="button" className="rd-btn rd-btn-primary" disabled={!canApprove} onClick={() => void approve()}>
          {busy ? "Waiting for Touch ID…" : "Approve with Touch ID"}
        </button>}
      </div>
    </div>
  </Modal>;
}
