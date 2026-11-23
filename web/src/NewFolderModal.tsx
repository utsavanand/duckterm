import { useState } from "react";
import { api } from "./api";
import { Button, Field, inputStyle, Modal, useToast } from "./ui";

// Create a folder for the left panel. Folders persist on the server even while
// empty, so you can make one and then drag sessions into it.
export function NewFolderModal({
  existing,
  onClose,
  onCreated,
}: {
  existing: string[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function create() {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (existing.includes(trimmed)) {
      toast(`Workspace "${trimmed}" already exists`, "err");
      return;
    }
    setBusy(true);
    try {
      const result = await api.createFolder(trimmed);
      toast(result.pending ? "Workspace creation pending sync. Open Settings → Collaboration to review." : `Created workspace ${trimmed}`);
      onCreated();
    } catch (e) {
      toast(`Create failed: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="New workspace" onClose={onClose}>
      <Field label="Workspace name">
        <input
          autoFocus
          style={inputStyle}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
          placeholder="e.g. payments"
        />
      </Field>
      <p className="rd-modal-hint">
        Then drag sessions onto this workspace in the left panel to organize them.
      </p>
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          marginTop: 8,
        }}
      >
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={create} disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create workspace"}
        </Button>
      </div>
    </Modal>
  );
}
