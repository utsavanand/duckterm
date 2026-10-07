import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ConversationCandidate, ConversationCandidates, ConversationIdentity, ConversationRecoveryService } from "./conversationRecoveryState";
import { conversationRecoveryService } from "./api";
import { hostName } from "./hostTransport";
import type { SessionView } from "./types";
import { identityPresentation, launchIdentity } from "./conversationRecoveryState";
import "./conversationRecovery.css";

export function ConversationIdentityNotice({ identity, computer, stopped, busy, onInstall, onChoose, onDetach }: {
  identity: ConversationIdentity; computer: string; stopped: boolean; busy: boolean;
  onInstall?: () => void; onChoose?: () => void; onDetach?: () => void;
}) {
  const view = identityPresentation(identity);
  return <section className="rd-conversation-health" aria-label="Conversation recovery" aria-live="polite">
    <h3 className={identity.status !== "recorded" ? "rd-conversation-warning" : ""}>{view.title}</h3>
    <p>{view.detail}</p>
    {identity.reason && <p>{identity.reason}</p>}
    <div className="rd-conversation-actions">
      {onInstall && identity.hooks.canInstall && identity.hooks.status === "missing" && <button className="rd-btn rd-btn-primary" disabled={busy} onClick={onInstall}>Install hooks</button>}
      {onChoose && stopped && identity.canAdopt && identity.status === "missing" && <button className="rd-btn rd-btn-ghost" disabled={busy} onClick={onChoose}>Choose a conversation</button>}
      {onDetach && stopped && identity.source === "adopted" && identity.canDetach && identity.revision && <button className="rd-btn rd-btn-ghost" disabled={busy} onClick={onDetach}>Undo attachment</button>}
    </div>
    {identity.hooks.canInstall && identity.hooks.status === "missing" && <p className="hint">Installs hooks globally on {computer} for this harness. If your harness settings are synced, other computers may receive them too. This does not recover a previously unrecorded conversation.</p>}
    <details><summary>Conversation details</summary><p>Computer: {computer}</p><p>{identity.source === "assigned" ? "Identity recorded at launch." : identity.source === "observed" ? "Identity observed from the harness." : identity.source === "adopted" ? "Conversation explicitly chosen by you." : "No conversation identity recorded."}</p><p>Hook configuration is separate from successful identity capture.</p></details>
  </section>;
}

function Prompts({ candidate }: { candidate: ConversationCandidate }) {
  return <dl className="rd-conversation-prompts"><dt>First prompt</dt><dd>{candidate.firstPrompt || "No prompt text available"}</dd><dt>Last prompt</dt><dd>{candidate.lastPrompt || "No prompt text available"}</dd></dl>;
}

export function ConversationRecoveryDialog({ service, sessionName, computer, project, onClose, onAdopted }: {
  service: ConversationRecoveryService; sessionName: string; computer: string; project?: string;
  onClose: () => void; onAdopted: (identity: ConversationIdentity) => void;
}) {
  const [list, setList] = useState<ConversationCandidates | null>(null);
  const [choice, setChoice] = useState<ConversationCandidate | null>(null);
  const [review, setReview] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true), [attempt, setAttempt] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null), closeButton = useRef<HTMLButtonElement>(null);
  const active = useRef(false), sending = useRef(false);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    active.current = true;
    dialog.current?.showModal(); closeButton.current?.focus();
    return () => { active.current = false; opener?.focus(); };
  }, []);
  useEffect(() => {
    let current = true;
    setLoading(true); setList(null); setChoice(null); setReview(false);
    void (async () => {
      // Re-read the binding before every list, including after a lost response.
      const identity = await service.identity();
      if (!current) return;
      if (identity.status !== "missing" || !identity.canAdopt) {
        onAdopted(identity);
        setError("This session is no longer available for recovery. Close this picker to review its current conversation status.");
        return;
      }
      const value = await service.candidates();
      if (current) setList(value);
    })()
      .catch((cause: Error) => { if (current) setError(`Could not load conversations: ${cause.message}`); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [service, attempt, onAdopted]);
  function reload() { setError(""); setAttempt(n => n + 1); }
  function close() { if (!sending.current) onClose(); }
  async function adopt() {
    if (sending.current || !review || !choice?.available || !list) return;
    sending.current = true; setBusy(true); setError("");
    try {
      const identity = await service.adopt(choice.handle, list.revision);
      if (active.current) { onAdopted(identity); onClose(); }
    } catch (cause) {
      if (active.current) {
        // A lost response might already have saved the choice. Refresh before
        // accepting another candidate; never automatically resubmit a mutation.
        setChoice(null); setReview(false); setList(null);
        setError(`Could not confirm the conversation choice: ${(cause as Error).message}. Check the session’s identity before choosing again.`);
      }
    } finally { sending.current = false; if (active.current) setBusy(false); }
  }
  return createPortal(<dialog ref={dialog} className="rd-conversation-dialog" aria-labelledby="conversation-recovery-title" onCancel={event => { event.preventDefault(); close(); }}>
    <div className="rd-conversation-shell"><header><div><p className="hint">{sessionName} · {computer}{project ? ` · ${project}` : ""}</p><h2 id="conversation-recovery-title">Choose the conversation to recover</h2></div><button ref={closeButton} className="rd-btn rd-btn-ghost" disabled={busy} onClick={close} aria-label="Close conversation picker">×</button></header>
      <div className="rd-conversation-body"><div className="rd-conversation-notice"><strong>DuckTerm cannot tell which conversation belongs to this session.</strong><p>These transcripts share its project folder. Compare the prompts and choose only a conversation you recognize. Nothing is selected automatically.</p></div>
        {error && <div className="rd-conversation-error" role="alert">{error}{!list && !loading && <button className="rd-btn rd-btn-ghost" onClick={reload}>Check again</button>}</div>}
        {loading && <p role="status">Loading conversations from {computer}…</p>}
        {!loading && list && list.candidates.length === 0 && <div className="rd-conversation-empty"><h3>{list.hasMore ? "No matching transcripts in this scan" : "No transcripts found for this project"}</h3><p>{list.hasMore ? "The scan limit was reached, so other transcripts may exist. Nothing was attached. Check the original computer." : "Nothing was attached. Check that the original transcript is still in this project on the original computer."}</p><button className="rd-btn rd-btn-ghost" onClick={reload}>Try again</button></div>}
        {list && list.candidates.length > 0 && !review && <><p className="hint">{list.candidates.length} conversations{list.hasMore ? " shown · scan limit reached; other transcripts may exist" : ""}</p><div className="rd-conversation-candidates">{list.candidates.map(candidate => <label key={candidate.handle} className={`rd-conversation-candidate${choice?.handle === candidate.handle ? " selected" : ""}`}><input type="radio" name="conversation-candidate" checked={choice?.handle === candidate.handle} disabled={!candidate.available || busy} onChange={() => { setChoice(candidate); setError(""); }} aria-label={candidate.label} /><div><div className="rd-conversation-candidate-title"><strong>{candidate.label}</strong><time dateTime={new Date(candidate.modifiedAt).toISOString()}>Modified {new Date(candidate.modifiedAt).toLocaleString()}</time></div><Prompts candidate={candidate} />{candidate.promptCount !== undefined && <small>{candidate.promptCount} prompts</small>}{candidate.reason && <p>{candidate.reason}</p>}</div></label>)}</div></>}
        {review && choice && <section className="rd-conversation-choice"><h3>Attach {choice.label} to {sessionName}?</h3><p className="hint">Modified {new Date(choice.modifiedAt).toLocaleString()}</p><Prompts candidate={choice} /><p>Your choice becomes this session’s recorded conversation. DuckTerm cannot verify the match from the shared folder alone. The agent stays stopped.</p></section>}
      </div><footer><p>Choosing a transcript does not launch or resume an agent.</p><div className="rd-conversation-actions">{review && <button className="rd-btn rd-btn-ghost" disabled={busy} onClick={() => setReview(false)}>Back</button>}<button className="rd-btn rd-btn-ghost" disabled={busy} onClick={close}>Cancel</button><button className="rd-btn rd-btn-primary" disabled={loading || busy || !choice?.available || !list} onClick={() => { if (review) void adopt(); else setReview(true); }}>{busy ? "Recording choice…" : review ? "Attach this conversation" : "Review selection"}</button></div></footer>
    </div>
  </dialog>, document.body);
}

export function SessionConversationRecovery({ session, stopped, onIdentity }: {
  session: SessionView; stopped: boolean; onIdentity: (value: ConversationIdentity) => void;
}) {
  const service = useMemo(() => conversationRecoveryService(session.key), [session.key]);
  const [identity, setIdentity] = useState<ConversationIdentity | null>(session.conversationIdentity ? launchIdentity(session.conversationIdentity) : null);
  const [choosing, setChoosing] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [uncertain, setUncertain] = useState(false), [notice, setNotice] = useState("");
  const mounted = useRef(true), mutating = useRef(false), generation = useRef(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const update = useCallback((value: ConversationIdentity) => {
    generation.current += 1; setIdentity(value); onIdentity(value);
    if (value.source === "adopted") setNotice("");
  }, [onIdentity]);
  useEffect(() => {
    let current = true;
    const refresh = async () => {
      if (document.hidden || mutating.current || choosing) return;
      const request = ++generation.current;
      try {
        const value = await service.identity();
        if (current && request === generation.current) { update(value); setError(""); setUncertain(false); }
      } catch (cause) {
        if (current && request === generation.current) setError(`Recovery status unavailable: ${(cause as Error).message}`);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => { current = false; clearInterval(timer); };
  }, [service, session.conversationIdentity?.status, stopped, retry, update, choosing]);
  async function install() {
    if (mutating.current) return;
    mutating.current = true; generation.current += 1; setBusy(true); setError("");
    try { const value = await service.installHooks(); if (mounted.current) update(value); }
    catch (cause) { if (mounted.current) setError(`Hook installation could not be confirmed: ${(cause as Error).message}. Check again before retrying.`); }
    finally { mutating.current = false; if (mounted.current) { setBusy(false); setRetry(n => n + 1); } }
  }
  async function detach() {
    if (mutating.current || uncertain || !stopped || identity?.source !== "adopted" || !identity.canDetach || !identity.revision) return;
    mutating.current = true; generation.current += 1; setBusy(true); setError(""); setNotice("");
    // Disable Resume while this mutation or its reconciliation is unresolved.
    update({ ...identity, canResume: false });
    try {
      const value = await service.detach(identity.revision);
      if (mounted.current) { update(value); setNotice("Attachment removed. The transcript is unchanged and the session stays stopped."); }
    } catch (cause) {
      if (mounted.current) { setUncertain(true); setError(`Undo could not be confirmed: ${(cause as Error).message}. Check the current status before trying again.`); }
    } finally {
      mutating.current = false;
      if (mounted.current) { setBusy(false); setRetry(n => n + 1); }
    }
  }
  const computer = session.hostLabel || hostName(session.key);
  return <>
    {identity && <ConversationIdentityNotice identity={identity} computer={computer} stopped={stopped} busy={busy || uncertain} onDetach={() => void detach()} onChoose={() => { generation.current += 1; setChoosing(true); }} onInstall={() => void install()} />}
    {notice && <p className="hint" role="status">{notice}</p>}
    {error && session.conversationIdentity && <p className="rd-conversation-error" role="status">{error}<button className="rd-btn rd-btn-ghost" onClick={() => setRetry(n => n + 1)}>Check again</button></p>}
    {choosing && <ConversationRecoveryDialog service={service} sessionName={session.label} computer={computer} project={session.worktreePath || session.cwd} onClose={() => setChoosing(false)} onAdopted={update} />}
  </>;
}
