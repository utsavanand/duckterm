import { desktop } from "./desktop";
import { SessionLocationDuck } from "./SessionLocationDuck";
import { HelperAgents } from "./HelperAgents";
import { ReactNode, useEffect, useState } from "react";
import { api } from "./api";
import { Duck, poseFor } from "./Duck";
import { TermMode, themesForMode } from "./termThemes";
import { contextLevel, effectiveState, fmtTokens } from "./sessions";
import { SessionView } from "./types";
import { useResumeSession } from "./useResumeSession";
import { useToast } from "./ui";

// The left panel: every session as a row, with forks nested under their parent
// via parentKey. Rows select sessions; actions live in the right-panel card.
export function AgentTree({
  sessions,
  now,
  folders: savedFolders,
  selectedKey,
  selectedFolder,
  onOpenFolder,
  onFolderRenamed,
  onFolderDeleted,
  onOpen,
  onOpenInbox,
  onFoldersChanged,
  onSessionMoved,
  onOpenGrid,
  onNewSessionIn,
  onOpenFolderInbox,
  folderThemes,
  onSetFolderTheme,
  termMode,
}: {
  sessions: SessionView[];
  now: number;
  folders: string[];
  selectedKey: string | null;
  selectedFolder?: string | null;
  onOpenFolder?: (folder: string) => void;
  onFolderRenamed?: (from: string, to: string) => void;
  onFolderDeleted?: (folder: string) => void;
  onOpen: (key: string) => void;
  onOpenInbox?: (key: string) => void;
  onFoldersChanged: () => void;
  onSessionMoved: (key: string, group: string) => void;
  onOpenGrid: (folder: string) => void;
  onNewSessionIn: (folder: string) => void;
  onOpenFolderInbox: (folder: string) => void;
  folderThemes: Record<string, string>;
  onSetFolderTheme: (folder: string, theme: string | null) => void;
  termMode: TermMode;
}) {
  const folders = [...new Set([...savedFolders, ...sessions.flatMap(session => {
    // Local folders come from the catalog; stale session snapshots must not
    // resurrect their old paths while a rename or move refresh is in flight.
    if (!session.host) return [];
    const parts = session.group?.split("/") ?? [];
    return parts.map((_, i) => parts.slice(0, i + 1).join("/"));
  })])];
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
      onFolderRenamed?.(name, r.to);
      toast(`Moved to ${r.to}`);
      onFoldersChanged();
    } catch (e) {
      // A conflicting destination may have been created in another window.
      // Refresh it even when this move failed so the owner can see it.
      onFoldersChanged();
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
      onFolderRenamed?.(path, next);
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
      onFolderDeleted?.(name);
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
      selectedKey={selectedKey}
      onOpen={onOpen}
      onOpenInbox={onOpenInbox}
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
      selected={selectedFolder === path}
      onOpen={() => onOpenFolder?.(path)}
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
  selected,
  onOpen,
  onDelete,
  name,
  depth,
  count,
  onDropSession,
  onDropFolder,
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
  selected: boolean;
  onOpen: () => void;
  depth: number;
  onRename: () => void;
  onDelete: () => void;
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
  // Start every folder closed when the dashboard opens or restarts.
  const [collapsed, setCollapsed] = useState(true);
  useEffect(() => {
    const reveal = (event: Event) => {
      const folder = (event as CustomEvent<string>).detail;
      if (folder === name || folder.startsWith(name + "/")) setCollapsed(false);
    };
    window.addEventListener("reveal-sidebar-folder", reveal);
    return () => window.removeEventListener("reveal-sidebar-folder", reveal);
  }, [name]);
  const [over, setOver] = useState(false);
  const leaf = name.split("/").pop();
  return (
    <div className={`rd-group${over ? " drop-over" : ""}`}>
      <div
        className={`rd-group-head${selected ? " selected" : ""}`}
        style={{ paddingLeft: 14 + depth * 16 }}
        draggable
        onDragStart={(e) => {
          e.stopPropagation();
          e.dataTransfer.setData("text/rd-folder", name);
          e.dataTransfer.effectAllowed = "move";
        }}
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
        <button className="rd-group-caret" aria-label={`${collapsed ? "Expand" : "Collapse"} ${name}`} aria-expanded={!collapsed} onClick={() => setCollapsed(c => !c)}>{collapsed ? "▸" : "▾"}</button>
        <button
          className="rd-group-name"
          aria-pressed={selected}
          onClick={onOpen}
          title="Double-click to rename"
          onDoubleClick={(e) => {
            e.stopPropagation();
            onRename();
          }}
        >
          {leaf}
        </button>
        <span className="rd-group-count">{count}</span>
        <button
          className="rd-group-phone"
          title={desktop() ? "View this folder’s interactions on This Mac" : "View folder interactions"}
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
  selectedKey,
  onOpen,
  onOpenInbox,
}: {
  node: Node;
  depth: number;
  indent?: number; // folder depth — visual offset only, unlike fork `depth`
  now: number;
  selectedKey: string | null;
  onOpen: (key: string) => void;
  onOpenInbox?: (key: string) => void;
}) {
  const s = node.session;
  const { resuming, resumeSession } = useResumeSession(s.key);
  const effState = effectiveState(s, now);
  const live = !["terminated", "stopped", "interrupted", "archived"].includes(effState);
  const stateLabel = effState;
  const ctxLevel = contextLevel(s.contextTokens, s.model);
  const hasChildren = node.children.length > 0;
  const [collapsed, setCollapsed] = useState(false);
  return (
    <>
      <div
        className={`rd-row${live ? "" : " terminated"}${ctxLevel ? ` ctx-${ctxLevel}` : ""}${s.key === selectedKey ? " selected" : ""}${s.inboxPending ? " has-inbox" : ""}`}
        title={`${s.label} · ${s.branch ? `${s.repoName ?? "repo"} · ${s.branch}` : (s.cwd ?? "—")} · ${s.runtime ?? "agent"} · ${stateLabel} · ${s.eventCount} events`}
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
          {desktop() ? <SessionLocationDuck key={s.key} session={s} pose={poseFor(effState, !!s.attentionSince)} /> : <Duck key={s.key} pose={poseFor(effState, !!s.attentionSince)} size={24} celebrating={s.celebration} />}
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
            <span className="rd-row-name">{s.label}</span>
            <span className={`rd-state st-${effState}`} title={stateLabel} aria-label={stateLabel}>{stateLabel}</span>
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
          {effState === "stopped" && s.launched && <button className="rd-row-resume"
            aria-label={`Resume ${s.label}`} disabled={resuming} onClick={resumeSession}>
            {resuming ? "Resuming…" : "Resume"}
          </button>}
        </div>
        <div className="rd-row-meta" onClick={() => onOpen(s.key)}>
          {s.branch ? `${s.repoName ?? "repo"} · ${s.branch}` : (s.cwd ?? "—")}
          {" · "}
          {s.eventCount} ev
        </div>
        <div className="rd-row-density-detail">
          {s.runtime ?? "agent"} · {effState === "waiting" ? "Waiting for input" : s.lastTool && effState === "busy" ? `Running ${s.lastTool}` : stateLabel}
        </div>
        {s.subagents && <HelperAgents agents={s.subagents} sessionKey={s.key} />}
      </div>
      {!collapsed &&
        node.children.map((child) => (
          <TreeRow
            key={child.session.key}
            node={child}
            depth={depth + 1}
            indent={indent}
            now={now}
            selectedKey={selectedKey}
            onOpen={onOpen}
            onOpenInbox={onOpenInbox}
          />
        ))}
    </>
  );
}
