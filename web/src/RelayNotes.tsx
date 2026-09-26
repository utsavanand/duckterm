import { useState } from "react";
import { api, RelayNote, RelayRule } from "./api";

function label(n: RelayNote): string {
  if (n.kind === "approval") return "Approval";
  if (n.kind === "choice") return "Question";
  return n.urgency === "offer" ? "Offer" : "Needs you";
}

function when(ts: number): string {
  const d = new Date(ts);
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

// What happened to a closed note, in words.
export function closedLine(n: RelayNote): string {
  if (n.status === "handled") {
    return n.kind === "question"
      ? "Answered in its terminal."
      : "Handled elsewhere, or the agent moved on.";
  }
  const by = n.answered_by && n.answered_by !== "owner" ? `Answered by ${n.answered_by}: ` : "";
  if (n.kind === "approval") {
    const verb = n.answer === "approve" ? "approved" : "denied";
    return `${by ? by + verb : "You " + verb}. ${n.route === "keystroke" ? "Oracle pressed the key in its terminal." : "Relayed through the approval request."}`;
  }
  if (n.kind === "choice") return `You chose "${n.answer}". Oracle selected it in its menu.`;
  const said = `${by ? by + "replied" : "You replied"} "${n.answer}".`;
  if (n.route === "inbox") {
    return `${said} It couldn't be typed right now${n.route_reason ? ` (${n.route_reason.split(".")[0].toLowerCase()})` : ""}, so it's in its inbox and Oracle will nudge it.`;
  }
  return `${said} Typed into its prompt.`;
}

export function NoteCard({
  note,
  rules,
  onChange,
  onPropose,
}: {
  note: RelayNote;
  rules: RelayRule[];
  onChange: () => void;
  onPropose: (text: string) => void;
}) {
  const [reply, setReply] = useState(note.suggestion?.reply ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const open = note.status === "open";
  const waiting = open && note.urgency !== "offer";
  const draftRule = note.suggestion ? rules.find((r) => r.id === note.suggestion?.rule_id) : undefined;

  async function answer(value: string | number, then?: () => void) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.relayAnswer(note.id, value);
      then?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      onChange();
    }
  }

  return (
    <div className={`rd-relay-note${waiting ? " open" : ""}${open && note.urgency === "offer" ? " offer" : ""}`} data-note={note.id} role="group" aria-label={`${label(note)} from ${note.name}`}>
      <div className="rd-relay-head">
        <span className="rd-relay-kind">{label(note)}</span>
        <b>{note.name}</b>
        <span className="rd-relay-meta">
          {[note.folder, note.runtime].filter(Boolean).join(" · ")} · {when(note.created_at)}
        </span>
      </div>
      {note.kind === "approval" && (
        <div>
          Wants to run <code>{note.detail || note.tool}</code>
          {note.tool && note.tool !== "Bash" && <span className="rd-relay-meta"> ({note.tool})</span>}
        </div>
      )}
      {note.kind === "choice" && note.question && <blockquote className="rd-relay-quote">{note.question}</blockquote>}
      {note.kind === "question" && note.question && <div className="rd-relay-ask">{note.question}</div>}
      {note.kind === "question" && note.excerpt && (
        <details className="rd-relay-excerpt">
          <summary>Show the agent's message</summary>
          <blockquote className="rd-relay-quote">{note.excerpt}</blockquote>
        </details>
      )}
      {note.detected_without_model && (
        <span className="rd-relay-meta">Spotted without Oracle's classifier (no model was available), so it may not need you.</span>
      )}
      {open && note.kind === "approval" && (
        <div className="rd-relay-actions">
          <button className="rd-btn rd-btn-primary rd-btn-sm" disabled={busy} onClick={() => void answer("approve")}>Approve</button>
          <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy} onClick={() => void answer("deny")}>Deny</button>
          <button
            className="rd-btn rd-btn-ghost rd-btn-sm"
            disabled={busy}
            onClick={() => void answer("approve", () => onPropose(`Always approve "${note.detail}"${note.folder ? ` in ${note.folder.split("/")[0]}` : ""}`))}
          >
            Approve and make a rule
          </button>
        </div>
      )}
      {open && note.kind === "choice" && (
        <div className="rd-relay-actions">
          {(note.options ?? []).slice(0, 9).map((o, i) => (
            <button key={o} className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy} onClick={() => void answer(i)}>
              {i + 1}. {o}
            </button>
          ))}
        </div>
      )}
      {open && note.kind === "question" && (note.options?.length ?? 0) > 0 && (
        <div className="rd-relay-actions">
          {(note.options ?? []).map((o) => (
            <button key={o} className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy} onClick={() => void answer(o)}>
              {o}
            </button>
          ))}
        </div>
      )}
      {open && note.kind === "question" && (
        <form
          className="rd-relay-reply"
          onSubmit={(e) => { e.preventDefault(); if (reply.trim()) void answer(reply.trim()); }}
        >
          <textarea
            aria-label={`Reply to ${note.name}`}
            placeholder={`Reply to ${note.name}`}
            rows={1}
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (reply.trim()) void answer(reply.trim());
              }
            }}
          />
          <button className="rd-btn rd-btn-primary rd-btn-sm" type="submit" disabled={busy || !reply.trim()}>Reply</button>
        </form>
      )}
      {open && draftRule && (
        <span className="rd-relay-meta">
          Drafted by {draftRule.id}. Send it unchanged {10 - (draftRule.streak ?? 0)} more time{10 - (draftRule.streak ?? 0) === 1 ? "" : "s"} and {draftRule.id} answers these on its own.
        </span>
      )}
      {!open && <span className="rd-relay-status">{closedLine(note)}</span>}
      {error && <span className="rd-oracle-error" role="alert">{error}</span>}
    </div>
  );
}

export function ProposalCard({
  rule,
  onCreated,
  onCancel,
}: {
  rule: RelayRule;
  onCreated: (rule: RelayRule) => void;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function create() {
    setBusy(true);
    setError("");
    try {
      onCreated((await api.createRule(rule)).rule);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  return (
    <div className="rd-relay-rule" role="group" aria-label="Proposed rule">
      <span className="rd-relay-kind rule">Proposed rule</span>
      <RuleFields rule={rule} />
      <div className="rd-relay-actions">
        <button className="rd-btn rd-btn-primary rd-btn-sm" disabled={busy} onClick={() => void create()}>Create rule</button>
        <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      {error && <span className="rd-oracle-error" role="alert">{error}</span>}
    </div>
  );
}

export function RuleFields({ rule }: { rule: RelayRule }) {
  return rule.kind === "approval" ? (
    <dl className="rd-relay-fields">
      <dt>When</dt>
      <dd>
        An agent asks to run {rule.tool ? `${rule.tool} ` : ""}
        {rule.command_pattern ? <>matching <code>{rule.command_pattern}</code></> : "anything"}
      </dd>
      <dt>Where</dt>
      <dd>{rule.folder ? `${rule.folder} folder` : "Any folder"}</dd>
      <dt>Do</dt>
      <dd>{rule.action === "approve" ? "Approve" : "Deny"}. Never covers chained commands or irreversible ones like force pushes and releases.</dd>
    </dl>
  ) : (
    <dl className="rd-relay-fields">
      <dt>When</dt>
      <dd>An agent's question contains {(rule.keywords ?? []).map((k) => `"${k}"`).join(" and ")}</dd>
      <dt>Reply</dt>
      <dd>"{rule.reply}"</dd>
      <dt>Mode</dt>
      <dd>
        {rule.mode === "live"
          ? "Sends on its own."
          : `Draft: Oracle pre-fills the reply and you tap Send, until you've sent it unchanged 10 times${rule.streak ? ` (${rule.streak} so far)` : ""}.`}
      </dd>
    </dl>
  );
}
