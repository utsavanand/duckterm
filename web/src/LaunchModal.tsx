import { useEffect, useMemo, useState } from "react";
import { api, BrowseResult, LaunchRequest } from "./api";
import { desktop, destinationRequest, selectLaunchTarget } from "./desktop";
import { RemoteProject, PreparedProject } from "./RemoteProject";
import { Button, Field, inputStyle, Modal, useToast } from "./ui";

// New session: a command (runtime is inferred from it), a path picked by
// browsing the filesystem (git-detected), an optional name + prompt.
// The agents we know how to launch, plus a custom escape hatch. Picking one
// sets the command to its binary; "custom" reveals a free-text command box for
// any other CLI agent (bring your own).
const AGENTS: { id: string; label: string; command: string }[] = [
  { id: "claude", label: "Claude Code", command: "claude" },
  { id: "codex", label: "Codex", command: "codex" },
  { id: "copilot", label: "Copilot", command: "copilot" },
  { id: "custom", label: "Custom…", command: "" },
];

export function LaunchModal({
  onClose,
  group,
}: {
  onClose: () => void;
  group?: string; // pre-assign the new session to this folder (folder + button)
}) {
  const toast = useToast();
  const native = desktop();
  const [, refreshTargets] = useState(0);
  useEffect(() => {
    const refresh = () => { refreshTargets(n => n + 1); setTarget(desktop()?.currentTarget ?? "local"); setPicked(null); setPrepared(null); setProjectKind("existing"); };
    window.addEventListener("desktop-targets-changed", refresh);
    return () => window.removeEventListener("desktop-targets-changed", refresh);
  }, []);
  const [projectKind, setProjectKind] = useState<"existing" | "copy" | "clone">("existing");
  const [prepared, setPrepared] = useState<PreparedProject | null>(null);
  const [transferBusy, setTransferBusy] = useState(false);
  const [target, setTarget] = useState(native?.currentTarget ?? "local");
  const elsewhere = !!native && target !== native.currentTarget;
  const destination = useMemo(() => elsewhere ? {
    browse: (path?: string) => destinationRequest<BrowseResult>(target, "browse", path ? { path } : {}),
    branches: (path: string) => destinationRequest<{ branches: string[] }>(target, "branches", { path }),
    zshThemes: () => destinationRequest<{ themes: string[] }>(target, "themes"),
    launch: (req: LaunchRequest) => {
      const params = Object.fromEntries(Object.entries(req).filter(([key, value]) => key !== "in_terminal" && value !== undefined));
      return destinationRequest<{ session_key: string }>(target, "launch", params);
    },
  } : api, [elsewhere, target]);
  const [agent, setAgent] = useState(native?.draft?.agent ?? "claude");
  const [command, setCommand] = useState(native?.draft?.command ?? "claude");
  const [name, setName] = useState(native?.draft?.name ?? "");
  const [prompt, setPrompt] = useState(native?.draft?.prompt ?? "");
  const [picked, setPicked] = useState<BrowseResult | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [busy, setBusy] = useState(false);
  // For a git folder: run in the folder as-is, or branch off into an isolated
  // worktree. No default — the user picks.
  const [mode, setMode] = useState<"in-place" | "worktree" | null>(null);
  const [branches, setBranches] = useState<string[]>([]);
  const [base, setBase] = useState("");
  const [newBranch, setNewBranch] = useState("");
  // oh-my-zsh prompt themes found on this machine; empty = hide the picker.
  const [zshThemes, setZshThemes] = useState<string[]>([]);
  const [zshTheme, setZshTheme] = useState("");

  useEffect(() => {
    let current = true;
    setZshThemes([]);
    setZshTheme("");
    destination.zshThemes()
      .then((d) => { if (current) setZshThemes(d.themes); })
      .catch(() => undefined);
    return () => { current = false; };
  }, [destination]);

  const path = picked?.path;
  const isGit = picked?.is_git ?? false;

  // Launch-time nudge: proposed AGENTS.md rules waiting in the picked folder.
  // The new agent won't see them (candidates don't render) — surfacing the
  // count here is the moment the user most cares about the folder's rules.
  const [pendingRules, setPendingRules] = useState(0);
  useEffect(() => {
    setPendingRules(0);
    if (!path || elsewhere) return;
    let stale = false;
    fetch(`/agents-md?dir=${encodeURIComponent(path)}`)
      .then((r) => r.json())
      .then((d: { rules?: { status: string }[] }) => {
        if (!stale)
          setPendingRules(
            (d.rules ?? []).filter((r) => r.status === "candidate").length,
          );
      })
      .catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [path, elsewhere]);

  // When a git folder is picked and the user wants a worktree, fetch the
  // branches to base off (local + remote, fetched fresh on the server).
  useEffect(() => {
    let current = true;
    if (mode === "worktree" && path) {
      setBranches([]);
      destination
        .branches(path)
        .then((d) => {
          if (!current) return;
          setBranches(d.branches);
          setBase(d.branches[0] ?? "");
        })
        .catch(() => undefined);
    }
    return () => { current = false; };
  }, [mode, path, destination]);

  async function submit() {
    if (!command.trim() || !path) {
      toast("A command and a folder are required", "err");
      return;
    }
    if (isGit && mode === null) {
      toast("Choose whether to run in place or in an isolated worktree", "err");
      return;
    }
    setBusy(true);
    try {
      // Worktree mode → repo_path (+ branch/base) so the server branches off.
      // In-place (or a plain folder) → cwd, touching nothing.
      const worktree = isGit && mode === "worktree";
      // Run the agent in a PTY Duckterm owns (in_terminal:false) so it renders
      // in the in-app terminal — no external iTerm/Terminal tab.
      const launched = prepared ? await destinationRequest<{ session_key: string }>(target, "project-launch", { id: prepared.id, command, name, prompt }) : await destination.launch({
        command,
        name: name || undefined,
        prompt: prompt || undefined,
        in_terminal: false,
        zsh_theme: zshTheme || undefined,
        ...(worktree
          ? {
              repo_path: path,
              branch: newBranch || undefined,
              base: base || undefined,
            }
          : { cwd: path }),
      });
      if (group && !elsewhere) await api.setGroup(launched.session_key, group);
      toast(group && !elsewhere ? `Started in ${group}` : `Started ${name || "session"}`);
      if (elsewhere) selectLaunchTarget(target, {});
      onClose();
    } catch (e) {
      toast(`Launch failed: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={group && !elsewhere ? `New session in ${group}` : "New session"} onClose={() => { if (!busy && !transferBusy) onClose(); }}>
      {native && (
        <Field label="Run on">
          <select
            aria-label="Run on"
            style={inputStyle}
            value={target}
            disabled={busy || transferBusy}
            onChange={(event) => {
              setTarget(event.target.value);
              setPrepared(null);
              setProjectKind("existing");
              setPicked(null);
              setMode(null);
              setBrowsing(false);
              setBranches([]);
              setBase("");
              setNewBranch("");
            }}
          >
            {native.targets.map((target) => (
              <option key={target.id} value={target.id}>{target.name}</option>
            ))}
          </select>
          <small style={{ color: "var(--muted)" }}>
            {target === "local"
              ? "Runs on this Mac."
              : "Keeps running remotely when you close the app or your laptop."}
          </small>
        </Field>
      )}
      <Field label="Agent">
        <div className="rd-agent-pick">
          {AGENTS.map((a) => (
            <button
              key={a.id}
              type="button"
              className={`rd-pill${agent === a.id ? " active" : ""}`}
              onClick={() => {
                setAgent(a.id);
                if (a.id !== "custom") setCommand(a.command);
              }}
            >
              {a.label}
            </button>
          ))}
        </div>
      </Field>

      {agent === "custom" && (
        <Field label="Command (any CLI agent — runtime is detected from it)">
          <input
            style={inputStyle}
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder='e.g. aider   ·   claude -p "fix the bug"'
            autoFocus
          />
        </Field>
      )}

      {native && target !== "local" && <Field label="Project source">
        <select aria-label="Project source" style={inputStyle} value={projectKind} disabled={busy || transferBusy} onChange={e => { setProjectKind(e.target.value as "existing" | "copy" | "clone"); setPicked(null); setPrepared(null); setMode(null); }}>
          <option value="existing">Existing remote folder</option><option value="copy">Copy local project</option><option value="clone">Clone Git repository</option>
        </select>
      </Field>}
      {projectKind !== "existing" && !prepared && <RemoteProject key={target + projectKind} target={target} kind={projectKind} command={command} onBusy={setTransferBusy} onPrepared={p => { setPrepared(p); setPicked({ path: p.destination, parent: null, is_git: false, entries: [] }); setMode("in-place"); }} />}
      {(projectKind === "existing" || prepared) && <>
      <Field label={native ? `Folder on ${native.targets.find((t) => t.id === target)?.name ?? target}` : "Folder to work in"}>
        {path ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 10px",
              border: "1px solid var(--border-strong)",
              borderRadius: 8,
              fontSize: 13,
            }}
          >
            <span className="mono" style={{ flex: 1, wordBreak: "break-all" }}>
              {path}
            </span>
            <span
              style={{
                color: isGit ? "var(--idle)" : "var(--muted)",
                whiteSpace: "nowrap",
              }}
            >
              {isGit ? "git repo" : "plain folder"}
            </span>
            <Button size="sm" variant="ghost" disabled={!!prepared || busy || transferBusy} onClick={() => setBrowsing(true)}>
              Change
            </Button>
          </div>
        ) : (
          <Button variant="ghost" onClick={() => setBrowsing(true)}>
            Browse…
          </Button>
        )}
      </Field>

      {pendingRules > 0 && (
        <div className="rd-rules-nudge">
          {pendingRules} proposed AGENTS.md rule{pendingRules > 1 ? "s" : ""}{" "}
          awaiting review in this folder — the new agent won't see them until
          accepted (AGENTS.md button, top bar).
        </div>
      )}

      {browsing && (
        <DirBrowser
          key={target}
          browse={destination.browse}
          start={path}
          onPick={(r) => {
            setPicked(r);
            setMode(null);
            setBrowsing(false);
          }}
          onCancel={() => setBrowsing(false)}
        />
      )}

      {path && isGit && (
        <Field label="How should this run?">
          <label className="rd-radio">
            <input
              type="radio"
              checked={mode === "in-place"}
              onChange={() => setMode("in-place")}
            />
            <span>
              <strong>Run in place</strong> — work directly in the folder, no
              branch or worktree created
            </span>
          </label>
          <label className="rd-radio">
            <input
              type="radio"
              checked={mode === "worktree"}
              onChange={() => setMode("worktree")}
            />
            <span>
              <strong>Isolated worktree</strong> — branch off into a separate
              checkout so several agents don't collide
            </span>
          </label>
        </Field>
      )}

      {path && isGit && mode === "worktree" && (
        <>
          <Field label="Base the new branch off">
            <select
              style={inputStyle}
              value={base}
              onChange={(e) => setBase(e.target.value)}
            >
              {branches.length === 0 && <option value="">Loading…</option>}
              {branches.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </Field>
          <Field label="New branch name (optional — auto-named if blank)">
            <input
              style={inputStyle}
              value={newBranch}
              onChange={(e) => setNewBranch(e.target.value)}
              placeholder="duckterm/login-refactor"
            />
          </Field>
        </>
      )}

      </>}
      <Field label="Name (optional)">
        <input
          style={inputStyle}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. login refactor"
        />
      </Field>
      {zshThemes.length > 0 && (
        <Field label="zsh prompt theme (optional — your oh-my-zsh themes)">
          <select
            className="rd-zsh-theme"
            style={inputStyle}
            value={zshTheme}
            onChange={(e) => setZshTheme(e.target.value)}
          >
            <option value="">default (your current zshrc)</option>
            {zshThemes.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Prompt / what you want it to do (optional)">
        <input
          style={inputStyle}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="add a healthcheck endpoint"
        />
      </Field>

      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          marginTop: 8,
        }}
      >
        <Button variant="ghost" onClick={onClose} disabled={busy || transferBusy}>
          Cancel
        </Button>
        <Button onClick={submit} disabled={busy || transferBusy}>
          {busy ? "Launching…" : "Launch"}
        </Button>
      </div>
    </Modal>
  );
}

// A simple server-backed folder navigator: lists subdirectories, flags git
// repos, lets you go up / into / select the current folder.
function DirBrowser({
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
