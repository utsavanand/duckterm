import { useEffect, useState } from "react";
import { BrowseResult } from "./api";
import { Button, inputStyle } from "./ui";

export function DirBrowser({
  browse,
  start,
  onPick,
  onCancel,
  pickLabel,
  createFolder,
  suggestedName = "",
  emptyOnly = false,
}: {
  browse: (path?: string) => Promise<BrowseResult>;
  start?: string;
  onPick: (r: BrowseResult) => void;
  onCancel: () => void;
  pickLabel?: string;
  createFolder?: (parent: string, name: string) => Promise<BrowseResult>;
  suggestedName?: string;
  emptyOnly?: boolean;
}) {
  const [data, setData] = useState<BrowseResult | null>(null);
  const [requestedPath, setRequestedPath] = useState(start);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [newFolder, setNewFolder] = useState(false);
  const [folderName, setFolderName] = useState(suggestedName);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  useEffect(() => {
    let current = true;
    setData(null);
    setError("");
    browse(requestedPath).then((result) => { if (current) setData(result); })
      .catch((e: Error) => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [browse, requestedPath, attempt]);

  if (!data) return <div role="status">
    <p>{error || "Connecting and loading folders…"}</p>
    {error && <Button size="sm" onClick={() => setAttempt((n) => n + 1)}>Retry</Button>}
    <Button size="sm" variant="ghost" onClick={onCancel}>Cancel browsing</Button>
  </div>;

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        marginBottom: 12,
        background: "var(--bg-soft)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 10px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <button
          className="rd-btn rd-btn-sm rd-btn-ghost"
          disabled={!data.parent || creating}
          onClick={() => data.parent && setRequestedPath(data.parent)}
        >
          ↑ Up
        </button>
        <span
          className="mono"
          style={{ flex: 1, fontSize: 12, wordBreak: "break-all" }}
        >
          {data.path}
        </span>
      </div>
      <div style={{ maxHeight: 200, overflowY: "auto", padding: 6 }}>
        {data.entries.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--muted)", padding: 8 }}>
            No subfolders.
          </div>
        )}
        {data.entries.map((e) => (
          <div
            key={e.path}
            onClick={() => { if (!creating) setRequestedPath(e.path); }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "5px 8px",
              borderRadius: 6,
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            <span>📁</span>
            <span style={{ flex: 1 }}>{e.name}</span>
            {e.is_git && (
              <span style={{ fontSize: 11, color: "var(--idle)" }}>git</span>
            )}
          </div>
        ))}
      </div>
      {emptyOnly && data.empty !== true && <p style={{ padding: "0 12px", fontSize: 13 }}>Choose an empty folder, or create a new one here. Existing files will not be overwritten.</p>}
      {createFolder && <div style={{ padding: "0 12px 12px" }}>
        {newFolder ? <>
          <label>New folder name<input aria-label="New folder name" style={inputStyle} value={folderName} disabled={creating} onChange={e => setFolderName(e.target.value)} /></label>
          <Button size="sm" disabled={creating || !folderName.trim()} onClick={async () => {
            setCreating(true); setCreateError("");
            try {
              const folder = await createFolder(data.path, folderName);
              setRequestedPath(folder.path); setNewFolder(false);
            } catch (e) { setCreateError((e as Error).message); }
            finally { setCreating(false); }
          }}>{creating ? "Creating…" : "Create folder"}</Button>
          <Button size="sm" variant="ghost" disabled={creating} onClick={() => { setNewFolder(false); setCreateError(""); }}>Cancel new folder</Button>
        </> : <Button size="sm" variant="ghost" onClick={() => { setNewFolder(true); setFolderName(suggestedName); }}>New folder…</Button>}
        {createError && <p role="alert">{createError}</p>}
      </div>}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 8,
          padding: "8px 10px",
          borderTop: "1px solid var(--border)",
        }}
      >
        <Button size="sm" variant="ghost" disabled={creating} onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" disabled={creating || (emptyOnly && data.empty !== true)} onClick={() => onPick(data)}>
          {pickLabel ?? `Use this folder${data.is_git ? " (git)" : ""}`}
        </Button>
      </div>
    </div>
  );
}
