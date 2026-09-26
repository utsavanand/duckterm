import { useEffect, useState } from "react";
import { BrowseResult } from "./api";
import { Button } from "./ui";

export function DirBrowser({
  browse,
  start,
  onPick,
  onCancel,
}: {
  browse: (path?: string) => Promise<BrowseResult>;
  start?: string;
  onPick: (r: BrowseResult) => void;
  onCancel: () => void;
}) {
  const [data, setData] = useState<BrowseResult | null>(null);
  const [requestedPath, setRequestedPath] = useState(start);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
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
          disabled={!data.parent}
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
            onClick={() => setRequestedPath(e.path)}
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
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 8,
          padding: "8px 10px",
          borderTop: "1px solid var(--border)",
        }}
      >
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={() => onPick(data)}>
          Use this folder{data.is_git ? " (git)" : ""}
        </Button>
      </div>
    </div>
  );
}
