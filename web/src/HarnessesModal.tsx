import { useEffect, useState } from "react";
import { api, authHeaders } from "./api";
import { Button, Field, inputStyle, Modal, useToast } from "./ui";

interface Harness {
  name: string;
  path: string;
  description?: string;
  args_choices?: Record<string, string[]>;
  uninstallable?: boolean;
  error?: string;
}

// Installable harnesses: suites of skills, hooks, and sub-agents (uv-suite is
// the canonical one) registered by path and installed into a project folder.
// The installer's output is shown verbatim — success or failure, you see what
// it did.
export function HarnessesModal({
  defaultDir,
  onClose,
}: {
  defaultDir: string | null;
  onClose: () => void;
}) {
  const toast = useToast();
  const [harnesses, setHarnesses] = useState<Harness[]>([]);
  const [regPath, setRegPath] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [output, setOutput] = useState<{ name: string; text: string } | null>(
    null,
  );

  const refresh = () =>
    api
      .harnesses()
      .then((d) => setHarnesses(d.harnesses))
      .catch(() => undefined);
  useEffect(() => {
    refresh();
  }, []);

  async function register() {
    if (!regPath.trim()) return;
    try {
      const r = await api.registerHarness(regPath.trim());
      toast(`Registered ${r.name}`);
      setRegPath("");
      refresh();
    } catch (e) {
      toast(`Register failed: ${(e as Error).message}`, "err");
    }
  }

  async function deregister(name: string) {
    try {
      await api.deregisterHarness(name);
      refresh();
    } catch (e) {
      toast(`Remove failed: ${(e as Error).message}`, "err");
    }
  }

  async function run(
    h: Harness,
    action: "install" | "uninstall",
    dir: string,
    args: string[],
  ) {
    setBusy(h.name);
    setOutput(null);
    try {
      const res = await fetch(
        `/harnesses/${encodeURIComponent(h.name)}/${action}`,
        {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ dir, args }),
        },
      );
      const d = (await res.json()) as {
        ok?: boolean;
        output?: string;
        error?: string;
      };
      setOutput({ name: h.name, text: d.output ?? d.error ?? "" });
      toast(
        d.ok
          ? `${action === "install" ? "Installed" : "Uninstalled"} ${h.name}`
          : `${action} failed`,
        d.ok ? undefined : "err",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <Modal title="Harnesses" onClose={onClose}>
      <p className="rd-harness-hint">
        A harness is an installable suite — skills, hooks, sub-agents,
        guardrails — that agents in a folder pick up. Register one by its
        directory (it needs a <code>duckterm-harness.json</code> or an{" "}
        <code>install.sh</code>), then install it into a project.
      </p>
      {harnesses.map((h) => (
        <HarnessRow
          key={h.name}
          harness={h}
          defaultDir={defaultDir}
          busy={busy === h.name}
          output={output?.name === h.name ? output.text : null}
          onRemove={() => deregister(h.name)}
          onRun={(action, dir, args) => run(h, action, dir, args)}
        />
      ))}
      {harnesses.length === 0 && (
        <p className="rd-panel-empty">No harnesses registered yet.</p>
      )}
      <Field label="Register a harness (directory path)">
        <div style={{ display: "flex", gap: 8 }}>
          <input
            style={{ ...inputStyle, flex: 1 }}
            value={regPath}
            placeholder="~/ws-my-projects/uv-suite"
            onChange={(e) => setRegPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && register()}
          />
          <Button onClick={register} disabled={!regPath.trim()}>
            Register
          </Button>
        </div>
      </Field>
    </Modal>
  );
}

function HarnessRow({
  harness,
  defaultDir,
  busy,
  output,
  onRun,
  onRemove,
}: {
  harness: Harness;
  defaultDir: string | null;
  busy: boolean;
  output: string | null;
  onRun: (action: "install" | "uninstall", dir: string, args: string[]) => void;
  onRemove: () => void;
}) {
  const [dir, setDir] = useState(defaultDir ?? "");
  const [args, setArgs] = useState("");
  const choiceFlags = Object.keys(harness.args_choices ?? {});
  const [choices, setChoices] = useState<Record<string, string>>({});

  // Manifest-declared pickers first (e.g. --persona sport), then free text.
  const argv = [
    ...choiceFlags.flatMap((flag) =>
      choices[flag] ? [flag, choices[flag]] : [],
    ),
    ...(args ? args.split(/\s+/) : []),
  ];

  return (
    <div className="rd-harness">
      <div className="rd-harness-head">
        <strong>{harness.name}</strong>
        <span className="rd-harness-desc">
          {harness.error ?? harness.description ?? ""}
        </span>
        <button
          className="rd-group-del"
          title="Remove from the registry (uninstalls nothing)"
          onClick={onRemove}
        >
          ✕
        </button>
      </div>
      <div className="rd-harness-path">{harness.path}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
        <input
          style={{ ...inputStyle, flex: 2, minWidth: 180 }}
          value={dir}
          placeholder="target project folder"
          onChange={(e) => setDir(e.target.value)}
        />
        {choiceFlags.map((flag) => (
          <select
            key={flag}
            style={{ ...inputStyle, flex: 1, minWidth: 120 }}
            value={choices[flag] ?? ""}
            title={flag}
            onChange={(e) =>
              setChoices((c) => ({ ...c, [flag]: e.target.value }))
            }
          >
            <option value="">{flag} (default)</option>
            {harness.args_choices![flag].map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        ))}
        <input
          style={{ ...inputStyle, flex: 1, minWidth: 120 }}
          value={args}
          placeholder="extra args"
          onChange={(e) => setArgs(e.target.value)}
        />
        <Button
          onClick={() => onRun("install", dir, argv)}
          disabled={busy || !dir.trim() || Boolean(harness.error)}
        >
          {busy ? "Working…" : "Install"}
        </Button>
        {harness.uninstallable && (
          <Button
            variant="ghost"
            onClick={() => onRun("uninstall", dir, argv)}
            disabled={busy || !dir.trim()}
          >
            Uninstall
          </Button>
        )}
      </div>
      {output !== null && <pre className="rd-harness-output">{output}</pre>}
    </div>
  );
}
