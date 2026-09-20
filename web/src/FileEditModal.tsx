import { useEffect, useState } from "react";
import { authHeaders } from "./api";
import { Button, Modal, useToast } from "./ui";

// Edit any text file in the session's folder without leaving the dashboard —
// built for the "put your API key in .env.xyz, don't paste it here" moment:
// secrets typed here go straight to disk via the local server, never through
// an agent's conversation or transcript. New dotfiles are written chmod 600.
export function FileEditModal({
  dir,
  onClose,
}: {
  dir: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const [path, setPath] = useState(`${dir}/.env`);
  const [text, setText] = useState("");
  const [exists, setExists] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  async function load(p: string) {
    setLoading(true);
    try {
      const res = await fetch(`/file?path=${encodeURIComponent(p)}`);
      const d = (await res.json()) as {
        text?: string;
        exists?: boolean;
        error?: string;
      };
      if (!res.ok) throw new Error(d.error ?? "load failed");
      setText(d.text ?? "");
      setExists(d.exists ?? false);
    } catch (e) {
      toast(`Load failed: ${(e as Error).message}`, "err");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(`${dir}/.env`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dir]);

  async function save() {
    setSaving(true);
    try {
      const res = await fetch("/file", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ path, text }),
      });
      if (!res.ok) throw new Error((await res.json()).error ?? "save failed");
      toast(`Saved ${path.split("/").pop()}`);
      onClose();
    } catch (e) {
      toast(`Save failed: ${(e as Error).message}`, "err");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title="Edit file" onClose={onClose}>
      <div className="rd-fileedit-bar">
        <input
          className="rd-fileedit-path"
          value={path}
          spellCheck={false}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") load(path);
          }}
        />
        <Button onClick={() => load(path)} disabled={loading}>
          {loading ? "Loading…" : "Load"}
        </Button>
      </div>
      <div className="rd-fileedit-hint">
        {exists
          ? "Editing the existing file."
          : "New file — it will be created on save (dotfiles get chmod 600)."}{" "}
        Secrets typed here go straight to disk, never through the agent.
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={loading ? "Loading…" : "KEY=value"}
        spellCheck={false}
        style={{
          width: "100%",
          height: 280,
          fontFamily: "ui-monospace, Menlo, monospace",
          fontSize: 13,
          lineHeight: 1.5,
          padding: 12,
          border: "1px solid var(--border)",
          borderRadius: 8,
          resize: "vertical",
          background: "var(--card)",
          color: "var(--text)",
        }}
      />
      <div
        style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}
      >
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={save} disabled={saving || !path.trim()}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </Modal>
  );
}
