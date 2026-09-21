import { useEffect, useState } from "react";
import { authHeaders } from "./api";
import { Button, Modal, useToast } from "./ui";

// The typed rule set for one folder (docs/agents-template.md):
// .duckterm-rules.json is the source of truth, AGENTS.md is rendered from it
// on save. Machine proposals (Suggest, digest bridge) arrive as candidates;
// only a human promotes them to active or demotes to rejected — rejected
// rules stay as tombstones so the same rule is never re-proposed.
export interface Rule {
  id: string;
  text: string;
  scope: string;
  status: "active" | "candidate" | "rejected";
  source: string;
  evidence: number;
  added: string;
  heading: string;
  note: string;
}

const SCOPES = ["all", "claude-code", "codex"];

function blankRule(text: string): Rule {
  return {
    id: text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)/g, "")
      .split("-")
      .slice(0, 6)
      .join("-"),
    text,
    scope: "all",
    status: "active",
    source: "manual",
    evidence: 1,
    added: new Date().toISOString().slice(0, 10),
    heading: "",
    note: "",
  };
}

export function AgentsMdModal({
  dir,
  onClose,
}: {
  dir: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const [rules, setRules] = useState<Rule[]>([]);
  const [legacyText, setLegacyText] = useState<string | null>(null); // hand-written AGENTS.md, not yet typed
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [suggesting, setSuggesting] = useState(false);

  useEffect(() => {
    fetch(`/agents-md?dir=${encodeURIComponent(dir)}`)
      .then((r) => r.json())
      .then((d: { rules?: Rule[]; text?: string; managed?: boolean }) => {
        setRules(d.rules ?? []);
        // A hand-written AGENTS.md that predates the typed format: offer an
        // import instead of silently replacing it on the first save.
        setLegacyText(!d.managed && d.text ? d.text : null);
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [dir]);

  const patch = (id: string, changes: Partial<Rule>) =>
    setRules((rs) => rs.map((r) => (r.id === id ? { ...r, ...changes } : r)));

  function importLegacy() {
    if (!legacyText) return;
    const items = legacyText
      .split("\n")
      .filter((ln) => ln.trimStart().startsWith("- "))
      .map((ln) => ln.trim().replace(/^- /, ""))
      .filter(Boolean);
    setRules((rs) => {
      const taken = new Set(rs.map((r) => r.id));
      return [...rs, ...items.map(blankRule).filter((r) => !taken.has(r.id))];
    });
    setLegacyText(null);
    toast(`${items.length} line(s) imported as active rules — review, then Save`);
  }

  // The observation loop: distill the corrections users gave agents in this
  // folder into scoped candidate rules. They land in the list for review —
  // nothing is written until Save.
  async function suggest() {
    setSuggesting(true);
    try {
      const res = await fetch("/agents-md/suggest", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ dir }),
      });
      const d = (await res.json()) as {
        suggestions?: Rule[];
        corrections_seen?: number;
        error?: string;
      };
      if (!res.ok) throw new Error(d.error ?? "suggest failed");
      const taken = new Set(rules.map((r) => r.id));
      const fresh = (d.suggestions ?? []).filter((s) => !taken.has(s.id));
      if (!fresh.length) {
        toast(
          d.corrections_seen
            ? "No new durable rules found in the corrections so far"
            : "No corrections observed in this folder yet",
        );
        return;
      }
      setRules((rs) => [...rs, ...fresh]);
      toast(`${fresh.length} candidate(s) added — review, then Save`);
    } catch (e) {
      toast(`Suggest failed: ${(e as Error).message}`, "err");
    } finally {
      setSuggesting(false);
    }
  }

  async function save() {
    setSaving(true);
    try {
      const res = await fetch("/agents-md", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ dir, rules }),
      });
      if (!res.ok) throw new Error((await res.json()).error ?? "save failed");
      toast("Rules saved — AGENTS.md re-rendered");
      onClose();
    } catch (e) {
      toast(`Save failed: ${(e as Error).message}`, "err");
    } finally {
      setSaving(false);
    }
  }

  function addDraft() {
    const rule = blankRule(draft.trim());
    if (rules.some((r) => r.id === rule.id)) {
      toast(`A rule with id "${rule.id}" already exists`, "err");
      return;
    }
    setRules((rs) => [...rs, rule]);
    setDraft("");
  }

  const candidates = rules.filter((r) => r.status === "candidate");
  const active = rules.filter((r) => r.status === "active");
  const rejected = rules.filter((r) => r.status === "rejected");

  return (
    <Modal title="AGENTS.md rules" onClose={onClose}>
      <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 8 }}>
        <code>{dir}</code> — saved to <code>.duckterm-rules.json</code>, rendered
        to <code>AGENTS.md</code>
      </div>

      {legacyText !== null && (
        <div className="rd-rules-legacy">
          This folder has a hand-written AGENTS.md. Import turns each list item
          into an active rule (the file is re-rendered on save).
          <Button variant="ghost" onClick={importLegacy}>
            Import {legacyText.split("\n").filter((l) => l.trimStart().startsWith("- ")).length}{" "}
            line(s)
          </Button>
        </div>
      )}

      <div className="rd-rules-list">
        {loading && <div className="rd-rules-empty">Loading…</div>}
        {!loading && rules.length === 0 && legacyText === null && (
          <div className="rd-rules-empty">
            No rules yet. Add one below, or distill them from past corrections.
          </div>
        )}

        {candidates.length > 0 && (
          <div className="rd-rules-section">Candidates — accept or reject</div>
        )}
        {candidates.map((r) => (
          <div key={r.id} className="rd-rule rd-rule-candidate">
            <div className="rd-rule-text">{r.text}</div>
            <div className="rd-rule-meta">
              {r.scope !== "all" && <span className="rd-rule-scope">{r.scope}</span>}
              <span title="source · sessions/corrections backing this rule">
                {r.source} ·{r.evidence}×
              </span>
              <Button variant="ghost" onClick={() => patch(r.id, { status: "active" })}>
                Accept
              </Button>
              <Button variant="ghost" onClick={() => patch(r.id, { status: "rejected" })}>
                Reject
              </Button>
            </div>
          </div>
        ))}

        {active.length > 0 && <div className="rd-rules-section">Active</div>}
        {active.map((r) => (
          <div key={r.id} className="rd-rule">
            <textarea
              className="rd-rule-edit"
              value={r.text}
              rows={2}
              spellCheck={false}
              onChange={(e) => patch(r.id, { text: e.target.value })}
            />
            <div className="rd-rule-meta">
              <select
                value={SCOPES.includes(r.scope) ? r.scope : "all"}
                onChange={(e) => patch(r.id, { scope: e.target.value })}
                title="Which agent runtime this rule applies to"
              >
                {SCOPES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <Button
                variant="ghost"
                onClick={() => setRules((rs) => rs.filter((x) => x.id !== r.id))}
              >
                Delete
              </Button>
            </div>
          </div>
        ))}

        {rejected.length > 0 && (
          <details className="rd-rules-rejected">
            <summary>Rejected ({rejected.length}) — kept so they're never re-proposed</summary>
            {rejected.map((r) => (
              <div key={r.id} className="rd-rule">
                <div className="rd-rule-text">{r.text}</div>
                <div className="rd-rule-meta">
                  <Button variant="ghost" onClick={() => patch(r.id, { status: "candidate" })}>
                    Restore
                  </Button>
                </div>
              </div>
            ))}
          </details>
        )}
      </div>

      <div className="rd-rules-add">
        <input
          value={draft}
          placeholder="New rule (active immediately)…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && draft.trim()) addDraft();
          }}
        />
        <Button variant="ghost" disabled={!draft.trim()} onClick={addDraft}>
          Add
        </Button>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
        <Button variant="ghost" onClick={suggest} disabled={suggesting || loading}>
          {suggesting ? "Observing…" : "Suggest from corrections"}
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={save} disabled={saving || loading}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </Modal>
  );
}
