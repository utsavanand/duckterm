import { hostName } from "./hostTransport";
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Button, Field, inputStyle, Modal, useToast } from "./ui";

interface Harness {
  name: string;
  path: string;
  description?: string;
  compatible?: string[];
  error?: string;
}

interface HarnessItem {
  kind: string;
  name: string;
  description: string;
}

// Skill kits (uv-suite is the canonical one) registered by path, with what
// each contains. Duckterm never runs a kit's own install script — installing
// returns with the declarative component format.
export function HarnessesModal({
  sessionKey = "",
  onClose,
}: {
  sessionKey?: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const [harnesses, setHarnesses] = useState<Harness[]>([]);
  const [regPath, setRegPath] = useState("");

  const refresh = useCallback(() =>
    api
      .harnesses(sessionKey)
      .then((d) => setHarnesses(d.harnesses))
      .catch(() => undefined), [sessionKey]);
  useEffect(() => {
    refresh();
  }, [refresh]);

  async function register() {
    if (!regPath.trim()) return;
    try {
      const r = await api.registerHarness(regPath.trim(), sessionKey);
      toast(`Added ${r.name}`);
      setRegPath("");
      refresh();
    } catch (e) {
      toast(`Add failed: ${(e as Error).message}`, "err");
    }
  }

  async function deregister(name: string) {
    try {
      await api.deregisterHarness(name, sessionKey);
      refresh();
    } catch (e) {
      toast(`Remove failed: ${(e as Error).message}`, "err");
    }
  }

  return (
    <Modal title={`Skill kits · ${hostName(sessionKey)}`} onClose={onClose}>
      <p className="rd-harness-hint">
        A skill kit is a collection of skills, hooks, sub-agents and
        guardrails that agents in a project pick up. Add one by its folder (it
        needs a <code>duckterm-harness.json</code>) to see what it contains.
        DuckTerm doesn't install kits yet — use the kit's own instructions.
      </p>
      {harnesses.map((h) => (
        <HarnessRow
          key={h.name}
          harness={h}
          sessionKey={sessionKey}
          onRemove={() => deregister(h.name)}
        />
      ))}
      {harnesses.length === 0 && (
        <p className="rd-panel-empty">No skill kits added yet.</p>
      )}
      <Field label="Add a skill kit (folder path)">
        <div style={{ display: "flex", gap: 8 }}>
          <input
            style={{ ...inputStyle, flex: 1 }}
            value={regPath}
            placeholder="~/ws-my-projects/uv-suite"
            onChange={(e) => setRegPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && register()}
          />
          <Button onClick={register} disabled={!regPath.trim()}>
            Add
          </Button>
        </div>
      </Field>
    </Modal>
  );
}

function HarnessRow({
  harness,
  sessionKey = "",
  onRemove,
}: {
  harness: Harness;
  sessionKey?: string;
  onRemove: () => void;
}) {
  const [items, setItems] = useState<HarnessItem[] | null>(null);
  const [showContents, setShowContents] = useState(false);

  async function toggleContents() {
    const next = !showContents;
    setShowContents(next);
    if (next && items === null) {
      try {
        const d = await api.harnessContents(harness.name, sessionKey);
        setItems(d.contents);
      } catch {
        setItems([]);
      }
    }
  }

  return (
    <div className="rd-harness">
      <div className="rd-harness-head">
        <strong>{harness.name}</strong>
        <span className="rd-harness-desc">
          {harness.error ?? harness.description ?? ""}
        </span>
        <button
          className="rd-group-del"
          title="Remove from this list (uninstalls nothing)"
          aria-label={`Remove ${harness.name} from this list`}
          onClick={onRemove}
        >
          ✕
        </button>
      </div>
      <div className="rd-harness-path">{harness.path}</div>
      <div className="rd-harness-compat">
        {harness.compatible && harness.compatible.length > 0
          ? `works with ${harness.compatible.join(", ")}`
          : "compatibility not declared"}
        <button className="rd-harness-contents-toggle" onClick={toggleContents}>
          {showContents ? "hide contents" : "view contents"}
        </button>
      </div>
      {showContents && (
        <div className="rd-harness-items">
          {items === null ? (
            <span className="rd-harness-desc">reading…</span>
          ) : items.length === 0 ? (
            <span className="rd-harness-desc">nothing recognizable found</span>
          ) : (
            ["skill", "sub-agent", "hook", "guardrail", "persona"].map(
              (kind) => {
                const of = items.filter((i) => i.kind === kind);
                if (of.length === 0) return null;
                return (
                  <div key={kind} className="rd-harness-kind">
                    <div className="rd-harness-kind-title">
                      {kind}s · {of.length}
                    </div>
                    {of.map((i) => (
                      <div key={i.name} className="rd-harness-item">
                        <span className="rd-harness-item-name">{i.name}</span>
                        <span className="rd-harness-item-desc">
                          {i.description || "—"}
                        </span>
                      </div>
                    ))}
                  </div>
                );
              },
            )
          )}
        </div>
      )}
    </div>
  );
}
