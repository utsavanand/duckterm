import { ReactNode, useEffect, useState } from "react";
import { api } from "./api";
import { Duck, poseFor } from "./Duck";
import { TermMode, themesForMode } from "./termThemes";
import { contextLevel, effectiveState, fmtTokens } from "./sessions";
import { SessionView } from "./types";
import { useToast } from "./ui";

// The left panel: every session as a row, with forks nested under their parent
// via parentKey. Clicking a row opens the detail drawer; actions sit inline.
export function AgentTree({
  sessions,
  now,
  labels,
  folders,
  selectedKey,
  onOpen,
  onOpenInbox,
  onFork,
  onDelete,
  onFoldersChanged,
  onSessionMoved,
  onRename,
  onOpenGrid,
  onNewSessionIn,
  onOpenFolderInbox,
  folderThemes,
  onSetFolderTheme,
  termMode,
}: {
  sessions: SessionView[];
  now: number;
  labels: Record<string, string>;
  folders: string[];
  selectedKey: string | null;
  onOpen: (key: string) => void;
  onOpenInbox?: (key: string) => void;
  onFork: (key: string) => void;
  onDelete: (key: string) => Promise<boolean>;
  onFoldersChanged: () => void;
  onSessionMoved: (key: string, group: string) => void;
  onRename: (key: string, name: string) => void;
  onOpenGrid: (folder: string) => void;
  onNewSessionIn: (folder: string) => void;
  onOpenFolderInbox: (folder: string) => void;
  folderThemes: Record<string, string>;
  onSetFolderTheme: (folder: string, theme: string | null) => void;
  termMode: TermMode;
}) {
  const toast = useToast();
  const roots = buildForest(sessions);

  // Drop a session onto a folder header (or the ungrouped zone) to move it there.
  async function moveToGroup(key: string, group: string) {
    // Optimistically reflect the move so the row jumps folders immediately; the
    // PATCH doesn't emit an SSE event, so without this the UI lags until refresh.
    onSessionMoved(key, group);
    try {
      await api.setGroup(key, group);
      toast(group ? `Moved to ${group}` : "Removed from folder");
      onFoldersChanged();
    } catch (e) {
      toast(`Move failed: ${(e as Error).message}`, "err");
    }
  }

  // Drag one folder onto another to nest it; onto the root zone (or the ⬆
  // header button) to un-nest.
  async function moveFolder(name: string, parent: string) {
    if (name === parent || parent.startsWith(name + "/")) return;
    try {
      const r = await api.moveFolder(name, parent);
      // Sessions follow the move server-side; mirror locally or they'd render
      // under a group path that no longer exists (invisible until reload).
      for (const s of sessions) {
        if (s.group === name) onSessionMoved(s.key, r.to);
        else if (s.group?.startsWith(name + "/"))
          onSessionMoved(s.key, r.to + s.group.slice(name.length));
      }
      toast(`Moved to ${r.to}`);
      onFoldersChanged();
    } catch (e) {
      toast(`Move failed: ${(e as Error).message}`, "err");
    }
  }

  async function renameFolder(path: string) {
    const leaf = path.split("/").pop() ?? path;
    const name = window.prompt(`Rename folder "${leaf}" to:`, leaf)?.trim();
    if (!name || name === leaf) return;
    if (name.includes("/")) {
      toast("Folder names can't contain '/'", "err");
      return;
    }
    const parent = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    const next = parent ? `${parent}/${name}` : name;
    try {
      await api.renameFolder(path, name);
      // Sessions follow the rename server-side; mirror it locally so rows
      // don't jump to Ungrouped until the next refetch.
      for (const s of sessions) {
        if (s.group === path) onSessionMoved(s.key, next);
        else if (s.group?.startsWith(path + "/"))
          onSessionMoved(s.key, next + s.group.slice(path.length));
      }
      onFoldersChanged();
      toast(`Renamed to ${next}`);
    } catch (e) {
      toast(`Rename failed: ${(e as Error).message}`, "err");
    }
  }

  async function createSubfolder(parent: string) {
    const name = window.prompt(`New folder inside "${parent}":`)?.trim();
    if (!name) return;
    try {
      await api.createFolder(`${parent}/${name.replaceAll("/", "-")}`);
      onFoldersChanged();
    } catch (e) {
      toast(`Create failed: ${(e as Error).message}`, "err");
    }
  }

  async function removeFolder(name: string) {
    if (
      !window.confirm(
        `Delete folder "${name}"? Its sessions return to Ungrouped.`,
      )
    )
      return;
    // Optimistically ungroup the folder's (and subfolders') sessions.
    for (const s of sessions) {
      if (s.group === name || s.group?.startsWith(name + "/"))
        onSessionMoved(s.key, "");
    }
    try {
      await api.deleteFolder(name);
      toast(`Deleted folder ${name}`);
      onFoldersChanged();
    } catch (e) {
      toast(`Delete failed: ${(e as Error).message}`, "err");
    }
  }

  // Group root sessions by their folder label; forks stay nested under their root.
  const ungrouped: Node[] = [];
  const byFolder = new Map<string, Node[]>();
  for (const node of roots) {
    const g = node.session.group;
    if (g) (byFolder.get(g) ?? byFolder.set(g, []).get(g)!).push(node);
    else ungrouped.push(node);
  }

  // `indent` is the FOLDER depth (visual only); TreeRow's own `depth` tracks
  // fork nesting — conflating them made grouped sessions render flush-left,
  // visually outside the folder that contains them.
  const renderNode = (node: Node, indent: number) => (
    <TreeRow
      key={node.session.key}
      node={node}
      depth={0}
      indent={indent}
      now={now}
      labels={labels}
      selectedKey={selectedKey}
      onOpen={onOpen}
      onOpenInbox={onOpenInbox}
      onFork={onFork}
      onDelete={onDelete}
      onRename={onRename}
      onUngroup={
        node.session.group
          ? () => moveToGroup(node.session.key, "")
          : undefined
      }
    />
  );

  const hasFolders = folders.length > 0;
  if (sessions.length === 0 && !hasFolders) {
    return <p className="rd-panel-empty">No agents yet.</p>;
  }

  // Folders nest by path ("orders/refunds"): render the tree recursively.
  const topLevel = folders.filter((f) => !f.includes("/"));
  const childrenOf = (path: string) =>
    folders.filter(
      (f) =>
        f.startsWith(path + "/") && !f.slice(path.length + 1).includes("/"),
    );

  // A folder's count is its whole SUBTREE (own sessions + every descendant
  // folder's) — a parent showing 0 while its child holds 2 read as empty.
  const subtreeCount = (path: string) =>
    [...byFolder.entries()].reduce(
      (n, [g, nodes]) =>
        g === path || g.startsWith(path + "/") ? n + nodes.length : n,
      0,
    );

  const renderFolder = (path: string, depth: number) => (
    <GroupHeader
      key={path}
      name={path}
      depth={depth}
      count={subtreeCount(path)}
      onDropSession={moveToGroup}
      onDropFolder={moveFolder}
      onDelete={() => removeFolder(path)}
      onRename={() => renameFolder(path)}
      onNewSubfolder={() => createSubfolder(path)}
      onNewSession={() => onNewSessionIn(path)}
      onUnnest={path.includes("/") ? () => moveFolder(path, "") : undefined}
      onOpenGrid={() => onOpenGrid(path)}
      onOpenInbox={() => onOpenFolderInbox(path)}
      theme={folderThemes[path]}
      onSetTheme={(t) => onSetFolderTheme(path, t)}
      termMode={termMode}
    >
      {(byFolder.get(path) ?? []).map((n) => renderNode(n, depth + 1))}
      {childrenOf(path).map((c) => renderFolder(c, depth + 1))}
    </GroupHeader>
  );

  return (
    <div className="rd-tree">
      {/* All folders render (even empty ones) so you can create then fill them. */}
      {topLevel.map((name) => renderFolder(name, 0))}
      {/* Ungrouped sessions sit at the root; dropping here clears the folder
          (or un-nests a dropped folder to the top level). */}
      <DropZone
        group=""
        onDropSession={moveToGroup}
        onDropFolder={moveFolder}
        active={hasFolders}
      >
        {ungrouped.map((n) => renderNode(n, 0))}
      </DropZone>
    </div>
  );
}

// A collapsible folder header: drop target for sessions AND folders, drag
// source for nesting, with subfolder + grid actions. Folders are paths; the
// header shows only the leaf name.
function GroupHeader({
  name,
  depth,
  count,
  onDropSession,
  onDropFolder,
  onDelete,
  onRename,
  onNewSubfolder,
  onNewSession,
  onUnnest,
  onOpenGrid,
  onOpenInbox,
  theme,
  onSetTheme,
  termMode,
  children,
}: {
  name: string;
  depth: number;
  onDelete: () => void;
  onRename: () => void;
  count: number;
  onDropSession: (key: string, group: string) => void;
  onDropFolder: (name: string, parent: string) => void;
  onNewSubfolder: () => void;
  onNewSession: () => void;
  onUnnest?: () => void; // set only for nested folders
  onOpenGrid: () => void;
  onOpenInbox: () => void;
  theme: string | undefined;
  onSetTheme: (theme: string | null) => void;
  termMode: TermMode;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [over, setOver] = useState(false);
  const leaf = name.split("/").pop();
  return (
    <div className={`rd-group${over ? " drop-over" : ""}`}>
      <div
        className="rd-group-head"
        style={{ paddingLeft: 14 + depth * 16 }}
        draggable
        onDragStart={(e) => {
          e.stopPropagation();
          e.dataTransfer.setData("text/rd-folder", name);
          e.dataTransfer.effectAllowed = "move";
        }}
        onClick={() => setCollapsed((c) => !c)}
        onDragOver={(e) => {
          if (
            e.dataTransfer.types.includes("text/rd-session") ||
            e.dataTransfer.types.includes("text/rd-folder")
          ) {
            e.preventDefault();
            e.stopPropagation();
            setOver(true);
          }
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOver(false);
          const key = e.dataTransfer.getData("text/rd-session");
          if (key) onDropSession(key, name);
          const folder = e.dataTransfer.getData("text/rd-folder");
          if (folder) onDropFolder(folder, name);
        }}
      >
        <span className="rd-group-caret">{collapsed ? "▸" : "▾"}</span>
        <span
          className="rd-group-name"
          title="Double-click to rename"
          onDoubleClick={(e) => {
            e.stopPropagation();
            onRename();
          }}
        >
          {leaf}
        </span>
        <span className="rd-group-count">{count}</span>
        <button
          className="rd-group-phone"
          title="View folder interactions"
          aria-label={`View interactions in ${name}`}
          onClick={(e) => { e.stopPropagation(); onOpenInbox(); }}
        >
          <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6 19.8 19.8 0 0 1-3.1-8.7A2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .3 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.4 1.8.6 2.8.7a2 2 0 0 1 1.8 2.1z" />
          </svg>
        </button>
        <span
          className={`rd-group-theme-wrap${theme ? " set" : ""}`}
          title={`Terminal theme for this folder: ${theme ?? "default"}`}
          onClick={(e) => e.stopPropagation()}
        >
          🎨
          <select
            className="rd-group-theme"
            value={theme ?? ""}
            onChange={(e) => onSetTheme(e.target.value || null)}
          >
            <option value="">default ({termMode})</option>
            {themesForMode(termMode).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </span>
        <button
          className="rd-group-grid"
          title="Open this folder's terminals in a grid"
          onClick={(e) => {
            e.stopPropagation();
            onOpenGrid();
          }}
        >
          ⛶
        </button>
        {onUnnest && (
          <button
            className="rd-group-unnest"
            title="Move this folder to the top level"
            onClick={(e) => {
              e.stopPropagation();
              onUnnest();
            }}
          >
            ⬆
          </button>
        )}
        <button
          className="rd-group-rename"
          title="Rename this folder (double-clicking the name works too)"
          onClick={(e) => {
            e.stopPropagation();
            onRename();
          }}
        >
          ✎
        </button>
        <button
          className="rd-group-add"
          title="New session in this folder"
          onClick={(e) => {
            e.stopPropagation();
            onNewSession();
          }}
        >
          +
        </button>
        <button
          className="rd-group-subfolder"
          title="New folder inside this one"
          onClick={(e) => {
            e.stopPropagation();
            onNewSubfolder();
          }}
        >
          ⊞
        </button>
        <button
          className="rd-group-del"
          title="Delete folder and its subfolders (sessions return to Ungrouped)"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          ✕
        </button>
      </div>
      {!collapsed && <div className="rd-group-body">{children}</div>}
    </div>
  );
}

// The catch-all zone for ungrouped sessions. Only a visible drop target when
// groups exist (otherwise it's just the plain list).
function DropZone({
  group,
  onDropSession,
  onDropFolder,
  active,
  children,
}: {
  group: string;
  onDropSession: (key: string, group: string) => void;
  onDropFolder: (name: string, parent: string) => void;
  active: boolean;
  children: ReactNode;
}) {
  const [over, setOver] = useState(false);
  return (
    <div
      className={`rd-dropzone${active ? " has-groups" : ""}${over ? " drop-over" : ""}`}
      onDragOver={(e) => {
        if (
          active &&
          (e.dataTransfer.types.includes("text/rd-session") ||
            e.dataTransfer.types.includes("text/rd-folder"))
        ) {
          e.preventDefault();
          setOver(true);
        }
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const key = e.dataTransfer.getData("text/rd-session");
        if (key) onDropSession(key, group);
        const folder = e.dataTransfer.getData("text/rd-folder");
        if (folder) onDropFolder(folder, "");
      }}
    >
      {active && <div className="rd-dropzone-label">Ungrouped</div>}
      {children}
    </div>
  );
}

interface Node {
  session: SessionView;
  children: Node[];
}

// Nest sessions by parentKey. A session whose parent isn't in the visible set
// (e.g. filtered out) becomes its own root so it's never hidden.
function buildForest(sessions: SessionView[]): Node[] {
  const byKey = new Map(sessions.map((s) => [s.key, s]));
  const nodes = new Map<string, Node>(
    sessions.map((s) => [s.key, { session: s, children: [] }]),
  );
  const roots: Node[] = [];
  for (const s of sessions) {
    const node = nodes.get(s.key)!;
    if (s.parentKey && byKey.has(s.parentKey)) {
      nodes.get(s.parentKey)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

function TreeRow({
  node,
  depth,
  indent = 0,
  now,
  labels,
  selectedKey,
  onOpen,
  onOpenInbox,
  onFork,
  onDelete,
  onRename,
  onUngroup,
}: {
  node: Node;
  depth: number;
  indent?: number; // folder depth — visual offset only, unlike fork `depth`
  now: number;
  labels: Record<string, string>;
  selectedKey: string | null;
  onOpen: (key: string) => void;
  onOpenInbox?: (key: string) => void;
  onFork: (key: string) => void;
  onDelete: (key: string) => Promise<boolean>;
  onRename: (key: string, name: string) => void;
  onUngroup?: () => void; // set only for grouped root sessions
}) {
  const toast = useToast();
  const s = node.session;
  const effState = effectiveState(s, now);
  const archived = effState === "archived";
  // "live" = actively running (Stop applies). stopped/terminated are not live but
  // are resumable for a launched session (we still have its worktree + id).
  const live = effState !== "terminated" && effState !== "stopped" && !archived;
  const resumable =
    (effState === "stopped" || effState === "terminated") && s.launched;
  // Stop and Archive only make sense for sessions Duckterm owns. A watched
  // session runs in a terminal we don't control, so Stop can't end it and
  // Archive would only hide a row whose agent keeps running — and unarchiving it
  // would offer a Resume that can't fire. Keep watched sessions observe-only.
  const canStop = live && s.launched;
  const canArchive = !archived && s.launched;
  // A terminated session's run is OVER: forking, notes, checkpoints, and
  // rename are workflow actions for something in progress — only Resume (if
  // resumable), Archive, and Delete apply.
  const ended = effState === "terminated";
  const stateLabel = effState; // "waiting" reads fine on its own
  const [notesOpen, setNotesOpen] = useState(false);
  const [notes, setNotes] = useState(s.notes ?? "");
  const [collapsed, setCollapsed] = useState(false);
  const [capturing, setCapturing] = useState(false);
  // Once stop/delete is in flight, grey the whole row's actions so a second
  // click can't fire a phantom request before the row is removed.
  const [ending, setEnding] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [archiving, setArchiving] = useState(false);
  // Delete is destructive (wipes history) — require a second, deliberate click:
  // the button arms ("Confirm delete?") then deletes. Auto-disarms after 4s.
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Double-click the name to rename in place — with several agents on one
  // repo, "ENTOURAGE" three times over is unusable.
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(s.label);
  async function saveRename() {
    setRenaming(false);
    const name = draft.trim();
    if (!name || name === s.label) return;
    onRename(s.key, name); // optimistic — PATCH emits no SSE event
    try {
      await api.updateSession(s.key, { name });
      toast("Renamed");
    } catch (e) {
      toast(`Rename failed: ${(e as Error).message}`, "err");
    }
  }
  const ctxLevel = contextLevel(s.contextTokens, s.model);
  const hasChildren = node.children.length > 0;
  // Branching is possible for any live session on a git repo (worktree fork or
  // promote) and for any live claude-code session (conversation fork, even with
  // no branch). The ForkModal picks the right sub-option.
  const canBranch = live && (Boolean(s.branch) || s.runtime === "claude-code");

  async function act(label: string, fn: () => Promise<unknown>) {
    try {
      await fn();
      toast(label);
    } catch (e) {
      toast(`${label} failed: ${(e as Error).message}`, "err");
    }
  }

  async function saveNotes() {
    if (notes === (s.notes ?? "")) return;
    await act("Notes saved", () => api.updateSession(s.key, { notes }));
    setNotesOpen(false);
  }

  async function stopSession() {
    if (ending) return;
    setEnding(true);
    try {
      await api.stop(s.key);
      toast("Stopped");
      // Leave it greyed — the resulting Stop/terminated event removes the row.
    } catch (e) {
      toast(`Stop failed: ${(e as Error).message}`, "err");
      setEnding(false); // let the user retry
    }
  }

  // Disarm the delete confirmation if the user doesn't follow through quickly.
  useEffect(() => {
    if (!confirmDelete) return;
    const t = setTimeout(() => setConfirmDelete(false), 4000);
    return () => clearTimeout(t);
  }, [confirmDelete]);

  async function requestDelete() {
    if (ending) return;
    if (!confirmDelete) {
      setConfirmDelete(true); // first click: arm
      return;
    }
    setConfirmDelete(false);
    setEnding(true);
    const deleted = await onDelete(s.key);
    if (!deleted) setEnding(false); // cancelled (e.g. unmerged confirm) or failed
  }

  async function archiveSession() {
    if (archiving) return;
    setArchiving(true);
    try {
      await api.archive(s.key);
      toast("Archived");
      // The archive event removes it from this view; no need to un-set.
    } catch (e) {
      toast(`Archive failed: ${(e as Error).message}`, "err");
      setArchiving(false);
    }
  }

  async function resumeSession() {
    if (resuming) return;
    setResuming(true);
    try {
      const r = await api.resume(s.key);
      toast(
        r.resumed ? "Resumed" : "Couldn't open a terminal to resume",
        r.resumed ? undefined : "err",
      );
    } catch (e) {
      toast(`Resume failed: ${(e as Error).message}`, "err");
    } finally {
      setResuming(false);
    }
  }

  async function captureCheckpoint() {
    if (capturing) return;
    // Capturing runs a summarizer agent (claude -p / codex / copilot), a few
    // seconds — show a spinner so the click doesn't feel dead.
    setCapturing(true);
    try {
      await act("Checkpoint recorded", () => api.checkpoint(s.key, "manual"));
    } finally {
      setCapturing(false);
    }
  }

  return (
    <>
      <div
        className={`rd-row${live ? "" : " terminated"}${notesOpen ? " expanded" : ""}${ctxLevel ? ` ctx-${ctxLevel}` : ""}${s.key === selectedKey ? " selected" : ""}${s.inboxPending ? " has-inbox" : ""}`}
        style={{ paddingLeft: 12 + indent * 16 + depth * 18 }}
        // Only root sessions are draggable into groups; forks follow their parent.
        draggable={depth === 0}
        onDragStart={(e) => {
          e.dataTransfer.setData("text/rd-session", s.key);
          e.dataTransfer.effectAllowed = "move";
        }}
      >
        <div className="rd-row-main">
          {hasChildren ? (
            <button
              className="rd-row-collapse"
              title={collapsed ? "Show forks" : "Hide forks"}
              onClick={(e) => {
                e.stopPropagation();
                setCollapsed((c) => !c);
              }}
            >
              {collapsed ? "▸" : "▾"}
            </button>
          ) : (
            depth > 0 && <span className="rd-row-twig">⑂</span>
          )}
          <Duck pose={poseFor(effState)} size={24} />
          <span className="rd-row-click" onClick={() => onOpen(s.key)}>
            {s.branch && (
              <span
                className="rd-row-git"
                title={
                  s.worktreePath
                    ? `Working in a git worktree on ${s.branch}`
                    : `On git branch ${s.branch}`
                }
              >
                ⎇
              </span>
            )}
            {renaming ? (
              <input
                className="rd-row-rename"
                autoFocus
                value={draft}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={saveRename}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveRename();
                  if (e.key === "Escape") setRenaming(false);
                }}
              />
            ) : (
              <span
                className="rd-row-name"
                title="Double-click to rename"
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  setDraft(s.label);
                  setRenaming(true);
                }}
              >
                {s.label}
              </span>
            )}
            <span className={`rd-state st-${effState}`}>{stateLabel}</span>
            {!!s.inboxPending && (
              <button
                className="rd-inbox-badge"
                title={`${s.inboxPending} pending questions — answer when ready`}
                aria-label={`Open ${s.label} inbox, ${s.inboxPending} pending`}
                onClick={(event) => { event.stopPropagation(); (onOpenInbox ?? onOpen)(s.key); }}
              >Inbox {s.inboxPending}</button>
            )}
            {ctxLevel && (
              <span
                className={`rd-ctx-chip ${ctxLevel}`}
                title={`${fmtTokens(s.contextTokens!)} tokens in context — ${
                  ctxLevel === "high"
                    ? "checkpoint or compact now"
                    : "consider a checkpoint soon"
                }`}
              >
                {fmtTokens(s.contextTokens!)}
              </span>
            )}
          </span>
        </div>
        <div className="rd-row-meta" onClick={() => onOpen(s.key)}>
          {s.branch ? `${s.repoName ?? "repo"} · ${s.branch}` : (s.cwd ?? "—")}
          {" · "}
          {s.eventCount} ev
        </div>
        {s.subagents && s.subagents.length > 0 && (
          <ul className="rd-subagents">
            {s.subagents.map((sa) => (
              <li
                key={sa.agent_id}
                className={`rd-subagent st-${sa.state}`}
                title={sa.agent_prompt ?? undefined}
              >
                <span className="rd-subagent-twig">↳</span>
                <span className={`dot ${sa.state === "running" ? "on" : ""}`} />
                <span className="rd-subagent-type">
                  {sa.agent_type ?? "subagent"}
                </span>
                {sa.agent_prompt && (
                  <span className="rd-subagent-task">{sa.agent_prompt}</span>
                )}
              </li>
            ))}
          </ul>
        )}
        <div className="rd-row-actions">
          {!ended && (
            <button
              className="rd-btn rd-btn-sm rd-btn-ghost"
              title="Rename this session (double-clicking the name works too)"
              onClick={() => {
                setDraft(s.label);
                setRenaming(true);
              }}
            >
              Rename
            </button>
          )}
          {onUngroup && (
            <button
              className="rd-btn rd-btn-sm rd-btn-ghost"
              title="Move this session out of its folder (dragging onto UNGROUPED works too)"
              onClick={onUngroup}
            >
              Ungroup
            </button>
          )}
          {/* One branching action: the modal offers a git worktree fork (or
              promotes an in-place session onto a branch) and, for claude-code,
              a conversation-only fork. */}
          {!archived && !ended && canBranch && (
            <button
              className="rd-btn rd-btn-sm rd-btn-ghost"
              title="Fork this session — into a git worktree, or fork the conversation"
              onClick={() => onFork(s.key)}
            >
              Fork
            </button>
          )}
          {!archived && !ended && (
            <button
              className={`rd-btn rd-btn-sm rd-btn-ghost${notesOpen ? " active" : ""}`}
              title="Personal notes for this session (local only)"
              onClick={() => setNotesOpen((o) => !o)}
            >
              Notes{s.notes ? " •" : ""}
            </button>
          )}
          {!archived && !ended && (
            <button
              className="rd-btn rd-btn-sm rd-btn-ghost"
              title="Record what was done so far"
              disabled={capturing}
              onClick={captureCheckpoint}
            >
              {capturing ? (
                <span className="rd-inline-spin">
                  <span className="rd-spinner" />
                  Capturing…
                </span>
              ) : (
                "Checkpoint"
              )}
            </button>
          )}
          {resumable && (
            <button
              className="rd-btn rd-btn-sm rd-btn-primary"
              title="Relaunch this session — continues the conversation for Claude Code"
              disabled={resuming}
              onClick={resumeSession}
            >
              {resuming ? (
                <span className="rd-inline-spin">
                  <span className="rd-spinner" />
                  Resuming…
                </span>
              ) : (
                "Resume"
              )}
            </button>
          )}
          {canStop && (
            <button
              className="rd-btn rd-btn-sm rd-btn-danger"
              disabled={ending}
              onClick={stopSession}
            >
              {ending ? (
                <span className="rd-inline-spin">
                  <span className="rd-spinner" />
                  Stopping…
                </span>
              ) : (
                "Stop"
              )}
            </button>
          )}
          {canArchive && (
            <button
              className="rd-btn rd-btn-sm rd-btn-ghost"
              title="Archive for good — history is kept, but the session leaves the list and can't be resumed (Stop is the pause)"
              disabled={archiving}
              onClick={archiveSession}
            >
              {archiving ? (
                <span className="rd-inline-spin">
                  <span className="rd-spinner" />
                  Archiving…
                </span>
              ) : (
                "Archive"
              )}
            </button>
          )}
          <button
            className={`rd-btn rd-btn-sm rd-btn-danger${confirmDelete ? " armed" : ""}`}
            title={
              confirmDelete
                ? "Click again to confirm"
                : s.launched || !live
                  ? "Delete this session and its history"
                  : "Stop watching — remove it from the dashboard (the agent keeps running in its own terminal)"
            }
            disabled={ending}
            onClick={requestDelete}
          >
            {confirmDelete
              ? "Confirm?"
              : s.launched || !live
                ? "Delete"
                : "Stop watching"}
          </button>
        </div>
        {notesOpen && (
          <div className="rd-row-notes-wrap">
            <textarea
              className="rd-row-notes"
              value={notes}
              placeholder="Notes for this session (local only)…"
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
            />
            <div className="rd-row-notes-bar">
              <span className="hint">
                {notes !== (s.notes ?? "") ? "Unsaved changes" : "Saved"}
              </span>
              <button
                className="rd-btn rd-btn-sm rd-btn-primary"
                disabled={notes === (s.notes ?? "")}
                onClick={saveNotes}
              >
                Save
              </button>
            </div>
          </div>
        )}
      </div>
      {!collapsed &&
        node.children.map((child) => (
          <TreeRow
            key={child.session.key}
            node={child}
            depth={depth + 1}
            indent={indent}
            now={now}
            labels={labels}
            selectedKey={selectedKey}
            onOpen={onOpen}
      onOpenInbox={onOpenInbox}
            onFork={onFork}
            onDelete={onDelete}
            onRename={onRename}
          />
        ))}
    </>
  );
}
