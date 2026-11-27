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
  kitsOnly = false,
}: {
  browse: (path?: string) => Promise<BrowseResult>;
  start?: string;
  onPick: (r: BrowseResult) => void;
  onCancel: () => void;
  pickLabel?: string;
  createFolder?: (parent: string, name: string) => Promise<BrowseResult>;
  suggestedName?: string;
  emptyOnly?: boolean;
  // Picking a MetaHarness: folders holding a metaharness.toml (or the old skill-kit.toml) are badged, the
  // pick button waits for one, and a folder with several manifests offers
  // each kit by name (the pick then carries that manifest's path).
  kitsOnly?: boolean;
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
    {error && <Button type="button" size="sm" onClick={() => setAttempt((n) => n + 1)}>Retry</Button>}
    <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Cancel browsing</Button>
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
          type="button"
          className="rd-btn rd-btn-sm rd-btn-ghost"
          disabled={!data.parent || creating}
          onClick={() => data.parent && setRequestedPath(data.parent)}
        >
          ↑ Up
        </button>
        <span
          className="mono"
          style={{ flex: 1, fontSize: "0.75rem", wordBreak: "break-all" }}
        >
          {data.path}
        </span>
      </div>
      <div style={{ maxHeight: 200, overflowY: "auto", padding: 6 }}>
        {data.entries.length === 0 && (
          <div style={{ fontSize: "0.75rem", color: "var(--muted)", padding: 8 }}>
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
              fontSize: "0.8125rem",
            }}
          >
            <span>📁</span>
            <span style={{ flex: 1 }}>{e.name}</span>
            {e.is_kit && (
              <span style={{ fontSize: "0.6875rem", color: "var(--brand-green)" }}>MetaHarness</span>
            )}
            {e.is_git && (
              <span style={{ fontSize: "0.6875rem", color: "var(--idle)" }}>git</span>
            )}
          </div>
        ))}
      </div>
      {kitsOnly && (data.kits ?? []).length > 1 && (
        <div style={{ padding: "0 12px 8px", borderTop: "1px solid var(--border)" }}>
          <p style={{ fontSize: "0.75rem", color: "var(--muted)", margin: "8px 0" }}>This folder holds {data.kits!.length} kits. Pick one:</p>
          {data.kits!.map((k) => (
            <div key={k.path} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: "0.8125rem" }}>
              <span style={{ flex: 1 }}>
                <strong>{k.name ?? k.file}</strong>{k.version ? ` ${k.version}` : ""}{k.description ? ` · ${k.description}` : ""}{k.error ? ` · ${k.error}` : ""}
                <span className="mono" style={{ display: "block", fontSize: "0.6875rem", color: "var(--muted)" }}>{k.file}</span>
              </span>
              <Button type="button" size="sm" disabled={!!k.error} onClick={() => onPick({ ...data, path: k.path })}>Use {k.name ?? k.file}</Button>
            </div>
          ))}
        </div>
      )}
      {kitsOnly && (data.kits ?? []).length === 0 && <p style={{ padding: "8px 12px 0", fontSize: "0.8125rem", color: "var(--muted)" }}>Not a MetaHarness: no metaharness.toml here. Open the folder that holds one; MetaHarness folders are marked.</p>}
      {emptyOnly && data.empty !== true && <p style={{ padding: "0 12px", fontSize: "0.8125rem" }}>Choose an empty folder, or create a new one here. Existing files will not be overwritten.</p>}
      {createFolder && <div style={{ padding: "0 12px 12px" }}>
        {newFolder ? <>
          <label>New folder name<input aria-label="New folder name" style={inputStyle} value={folderName} disabled={creating} onChange={e => setFolderName(e.target.value)} /></label>
          <Button type="button" size="sm" disabled={creating || !folderName.trim()} onClick={async () => {
            setCreating(true); setCreateError("");
            try {
              const folder = await createFolder(data.path, folderName);
              setRequestedPath(folder.path); setNewFolder(false);
            } catch (e) { setCreateError((e as Error).message); }
            finally { setCreating(false); }
          }}>{creating ? "Creating…" : "Create folder"}</Button>
          <Button type="button" size="sm" variant="ghost" disabled={creating} onClick={() => { setNewFolder(false); setCreateError(""); }}>Cancel new folder</Button>
        </> : <Button type="button" size="sm" variant="ghost" onClick={() => { setNewFolder(true); setFolderName(suggestedName); }}>New folder…</Button>}
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
        <Button type="button" size="sm" variant="ghost" disabled={creating} onClick={onCancel}>
          Cancel
        </Button>
        {!(kitsOnly && (data.kits ?? []).length > 1) && (
          <Button type="button" size="sm" disabled={creating || (emptyOnly && data.empty !== true) || (kitsOnly && (data.kits ?? []).length === 0)} onClick={() => onPick(data)}>
            {pickLabel ?? `Use this folder${data.is_git ? " (git)" : ""}`}
          </Button>
        )}
      </div>
    </div>
  );
}
