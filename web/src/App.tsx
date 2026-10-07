import { SessionActions } from "./SessionActions";
import type { SessionActionAnchor } from "./SessionActionMenu";
import { useDesktopNotifications } from "./useDesktopNotifications";
import { SidebarFilterToggle } from "./SidebarFilters";
import { useSidebarFilters } from "./sidebarFilterState";
import duckMark from "./assets/duckmark.svg?no-inline";
import { ArchiveUndo, useArchiveRequests } from "./ArchiveUndo";
import { sessionFetch, sessionRef } from "./hostTransport";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentsMdModal } from "./AgentsMdModal";
import { FolderView } from "./FolderView";
import { api } from "./api";
import { desktop, openNativeBugReport } from "./desktop";
import { Connectors } from "./Connectors";
import { ContextViews } from "./ContextViews";
import { Analytics } from "./Analytics";
import { AnalyticsTab } from "./analyticsData";
import { useRelayCount } from "./relay";
import { ForkModal } from "./ForkModal";
import { GridView } from "./GridView";
import { BackupModal } from "./BackupModal";
import { BugReport } from "./BugReport";
import { DashboardMenus } from "./DashboardMenus";
import { LiveAgentTree, LiveControlTower, LiveContextPanel, LiveInboxView, LiveSessionCard } from "./liveSessionViews";
import { HarnessesModal } from "./HarnessesModal";
import { TimelineView } from "./TimelineView";
import { ArtifactsView } from "./ArtifactsView";
import { InboxView } from "./InboxView";
import { MessageFolderModal } from "./MessageFolderModal";
import { useInboxCounts } from "./useInboxCounts";
import { MoveRemoteModal } from "./RemoteProject";
import { LaunchModal } from "./LaunchModal";
import { Messages } from "./Messages";
import { MessagePinStrip, PinTarget, useMessagePins } from "./MessagePins";
import { NewFolderModal } from "./NewFolderModal";
import { Terminal } from "./Terminal";
import { SessionShell } from "./SessionShell";
import { useTerminalCache } from "./terminalCache";
import { PanelToggle, useSidePanels } from "./SidePanels";
import { SessionView } from "./types";
import {
  TermMode,
  ThemeOverrides,
  loadTermThemes,
  loadThemeOverrides,
  resolveTermTheme,
  saveTermThemes,
  saveThemeOverrides,
  themesForMode,
} from "./termThemes";
import { Modal, ToastProvider, useToast } from "./ui";
import { useEventStream } from "./useEventStream";
import { useSessionSelection } from "./useSessionSelection";
import { useTheme } from "./useTheme";
import { useSidebarDensity } from "./useSidebarDensity";
import { useFolders } from "./useFolders";
import "./sidebarDensity.css";
import { useAttended } from "./useAttended";

function Dashboard() {
  const { sessions: sourceSessions, connected, loadedHosts, removeSessions, patchSession } =
    useEventStream();
  const inboxCounts = useInboxCounts();
  const archives = useArchiveRequests();
  const sidePanels = useSidePanels();
  const sidebarFilters = useSidebarFilters();
  const sessions = useMemo(
    () => sourceSessions.filter(s => !archives.requests.some(r => r.session_key === s.key)).map((s) => ({ ...s, inboxPending: inboxCounts[s.key] ?? 0 })),
    [sourceSessions, inboxCounts, archives.requests],
  );
  const toast = useToast();
  const { theme, resolved: mode, setTheme } = useTheme();
  const { density, setDensity } = useSidebarDensity();

  const [modal, setModal] = useState<
    "launch" | "agentsmd" | "folder" | "harnesses" | "backup" | "bugreport" | null
  >(desktop()?.draft ? "launch" : null);
  const [bugSession, setBugSession] = useState<string | null>(null);
  const [towerOpen, setTowerOpen] = useState(false);
  const [analyticsTab, setAnalyticsTab] = useState<AnalyticsTab | null>(null);
  const relayOpen = useRelayCount();
  const defaultSelection = sessions.find(s => s.state !== "archived")?.key ?? null;
  const { selectedKey, selectSession } = useSessionSelection(sessions, defaultSelection, loadedHosts);
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const setSelectedKey = useCallback((key: string | null) => {
    setSelectedFolder(null);
    selectSession(key);
  }, [selectSession]);
  useEffect(() => {
    const select = (event: Event) => {
      const key = (event as CustomEvent<string>).detail;
      setSelectedKey(key);
    };
    const selectNative = (event: Event) => {
      const detail = (event as CustomEvent<{host:string; key:string}>).detail;
      select(new CustomEvent("select-host-session", { detail: sessionRef(detail.host, detail.key) }));
    };
    window.addEventListener("native-select-session", selectNative);
    window.addEventListener("select-host-session", select);
    return () => { window.removeEventListener("select-host-session", select); window.removeEventListener("native-select-session", selectNative); };
  }, [setSelectedKey]);
  const messagePins = useMessagePins(selectedKey);
  const [pinTarget, setPinTarget] = useState<(PinTarget & { sessionKey: string }) | null>(null);
  const pinSequence = useRef(0);
  const [moveKey, setMoveKey] = useState<string | null>(null);
  useEffect(() => {
    const move = (event: Event) => setMoveKey((event as CustomEvent<string>).detail);
    window.addEventListener("move-to-remote", move);
    return () => window.removeEventListener("move-to-remote", move);
  }, []);
  const moveSession = sessions.find(s => s.key === moveKey);
  const [actionAnchor, setActionAnchor] = useState<SessionActionAnchor | null>(null);
  const [notesKey, setNotesKey] = useState<string | null>(null);
  useEffect(() => {
    const show = (event: Event) => setActionAnchor((event as CustomEvent<SessionActionAnchor>).detail);
    window.addEventListener("session-actions", show);
    return () => window.removeEventListener("session-actions", show);
  }, []);
  const actionSession = sessions.find(s => s.key === actionAnchor?.key);
  const actionAnchorRef = useRef(actionAnchor); actionAnchorRef.current = actionAnchor;
  const closeActions = () => { if (actionAnchorRef.current !== actionAnchor) return; actionAnchor?.trigger.focus(); setActionAnchor(null); };
  const [forkKey, setForkKey] = useState<string | null>(null);
  // Folder the next launched session should land in (folder + button).
  const [launchGroup, setLaunchGroup] = useState<string | undefined>(undefined);
  const [checkpointTarget, setCheckpointTarget] = useState<{ key: string; request: number } | null>(null);
  const [view, setView] = useState<"terminal" | "messages" | "history" | "inbox" | "artifacts">(
    "terminal",
  );
  const [messageFolder, setMessageFolder] = useState<string | null>(null);
  const [inboxFolder, setInboxFolder] = useState<string | null>(null);
  // The folder whose terminals are tiled fullscreen; null = grid closed.
  const [gridFolder, setGridFolder] = useState<string | null>(null);
  const [focusOpen, setFocusOpen] = useState(false);
  const pinnedSessions = sessions.filter((s) => s.pinned);
  useEffect(() => {
    if (focusOpen && pinnedSessions.length === 0) setFocusOpen(false);
  }, [focusOpen, pinnedSessions.length]);
  async function toggleSessionPin(s: SessionView) {
    try {
      const saved = await api.setFocusPin(s.key, !s.pinned);
      patchSession(s.key, saved);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }
  // Terminal color theme, per app mode: the terminal follows the light/dark
  // toggle, and each mode remembers its own pick ("auto" = the mode default).
  const [termThemes, setTermThemes] =
    useState<Record<TermMode, string>>(loadTermThemes);
  useEffect(() => {
    saveTermThemes(termThemes);
  }, [termThemes]);
  const termTheme = termThemes[mode];
  const setTermTheme = (pick: string) =>
    setTermThemes((p) => ({ ...p, [mode]: pick }));
  // Per-session / per-folder theme overrides (session > nearest folder > global).
  const [themeOverrides, setThemeOverrides] =
    useState<ThemeOverrides>(loadThemeOverrides);
  useEffect(() => {
    saveThemeOverrides(themeOverrides);
  }, [themeOverrides]);
  const themeFor = (s: SessionView) =>
    resolveTermTheme(themeOverrides, termTheme, mode, s.key, s.group);
  const setSessionTheme = (key: string, theme: string | null) =>
    setThemeOverrides((o) => {
      const sessions = { ...o.sessions };
      if (theme) sessions[key] = theme;
      else delete sessions[key];
      return { ...o, sessions };
    });
  const setFolderTheme = (folder: string, theme: string | null) =>
    setThemeOverrides((o) => {
      const folders = { ...o.folders };
      if (theme) folders[folder] = theme;
      else delete folders[folder];
      return { ...o, folders };
    });

  const { folders, refreshFolders } = useFolders();
  useEffect(() => {
    void refreshFolders();
  }, [sessions.length, refreshFolders]);

  async function deleteSession(key: string): Promise<boolean> {
    try {
      let res = await api.remove(key);
      if (res.status === 409 && res.unmerged_commits) {
        const ok = window.confirm(
          `Branch ${res.branch} has ${res.unmerged_commits} commit(s) not merged into main. ` +
            `Delete anyway and discard that work?`,
        );
        if (!ok) return false;
        res = await api.remove(key, true);
      }
      removeSessions([key]);
      if (selectedKey === key) setSelectedKey(null);
      toast("Deleted");
      return true;
    } catch (e) {
      toast(`Delete failed: ${(e as Error).message}`, "err");
      return false;
    }
  }

  // Waiting and archived membership never change during idle settling. Read
  // raw state here; only the time-rendering children need effectiveState.
  const agents = useMemo(
    () => sessions.filter((s) => s.state !== "archived"),
    [sessions],
  );

  // "Needs you": the whole point of a fleet view is not staring at it. The
  // count rides the tab title; sessions that ENTER waiting fire a browser
  // notification (if the user granted permission via the bell).
  const waiting = useMemo(
    () => sessions.filter((s) => s.state === "waiting"),
    [sessions],
  );
  const notificationSessions = useMemo(() => sessions.map(s => ({ key: s.key, label: s.label,
    waiting: s.state === "waiting" })), [sessions]);
  const notifications = useDesktopNotifications(notificationSessions, loadedHosts);
  useEffect(() => {
    document.title = waiting.length ? `(${waiting.length}) DuckTerm` : "DuckTerm";
  }, [waiting.length]);

  const selected = sessions.find((s) => s.key === selectedKey) ?? null;
  useAttended(selected?.key ?? null, !!selected?.attentionSince);
  const forkSession = sessions.find((s) => s.key === forkKey) ?? null;
  // Grid membership includes every owned PTY; the single-session view keeps
  // only recently visited terminals mounted so hidden output stays bounded.
  const terminalAgents = useMemo(
    // Pending archives leave their cached terminal mounted during Undo.
    () => sourceSessions.filter(s => s.ptyOwned && s.state !== "archived"),
    [sourceSessions],
  );

  const mountedTerminalKeys = useTerminalCache(
    terminalAgents.map(s => s.key), view === "terminal" && selectedFolder === null ? selectedKey : null,
  );
  const mountedTerminals = terminalAgents.filter(s => mountedTerminalKeys.includes(s.key));

  // The selected agent's working directory anchors AGENTS.md (per-folder file).
  const agentsMdDir = selected?.worktreePath ?? selected?.cwd ?? null;

  // Pending rule candidates for that folder badge the AGENTS.md button — the
  // recurring-review nudge. Re-fetched when the editor closes (a save may
  // have promoted/rejected them all).
  const [ruleCandidates, setRuleCandidates] = useState(0);
  useEffect(() => {
    if (!agentsMdDir || modal === "agentsmd") return;
    let stale = false;
    sessionFetch(selected?.key ?? "", `/agents-md?dir=${encodeURIComponent(agentsMdDir)}`)
      .then((r) => r.json())
      .then((d: { rules?: { status: string }[] }) => {
        if (!stale)
          setRuleCandidates(
            (d.rules ?? []).filter((r) => r.status === "candidate").length,
          );
      })
      .catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [agentsMdDir, modal, selected?.key]);

  return (
    <div className="rd-app" data-density={density}>
      <ArchiveUndo {...archives} />
      <header className="rd-topbar">
        <span className="rd-brand">
          <img
            className="rd-brand-mark"
            src={duckMark}
            alt=""
            width={22}
            height={22}
          />
          <span className="rd-brand-name">Duck<span className="rd-brand-term">Term</span></span>
        </span>
        <span className="rd-live">
          <span className={`dot ${connected ? "on" : "off"}`} />
          {connected ? "Live" : "Disconnected"}
        </span>
        <span className="rd-spacer" />
        <button className={`rd-btn rd-btn-ghost rd-btn-sm${focusOpen ? " rd-btn-active" : ""}`}
          disabled={!pinnedSessions.length} aria-pressed={focusOpen}
          title={pinnedSessions.length ? "Open pinned terminals" : "Pin a session to open Focus"}
          onClick={() => { setFocusOpen((open) => !open); setTowerOpen(false); }}>
          Focus · {pinnedSessions.length}
        </button>
        <button
          className={`rd-btn rd-btn-ghost rd-btn-sm${towerOpen ? " rd-btn-active" : ""}`}
          aria-pressed={towerOpen}
          onClick={() => { setTowerOpen((o) => !o); setFocusOpen(false); setGridFolder(null); }}
          title="Control tower: fleet insights, every agent at a glance, and Oracle chat"
        >
          Oracle
          {relayOpen > 0 && <span className="rd-rules-badge" aria-label={`${relayOpen} need you`}>{relayOpen}</span>}
        </button>
        <button
          className="rd-btn rd-btn-ghost rd-btn-sm"
          onClick={() => setModal("agentsmd")}
          disabled={!agentsMdDir}
          title={
            agentsMdDir
              ? ruleCandidates
                ? `${ruleCandidates} proposed rule(s) awaiting review`
                : "Edit the AGENTS.md rules for this agent's folder"
              : "Select an agent to edit its AGENTS.md"
          }
        >
          AGENTS.md
          {ruleCandidates > 0 && (
            <span className="rd-rules-badge">{ruleCandidates}</span>
          )}
        </button>
        <DashboardMenus sessions={sessions} density={density} onDensity={setDensity} theme={theme} onTheme={setTheme} termMode={mode} termTheme={termTheme} onTermTheme={setTermTheme} notifyOn={notifications.on} onNotify={() => void notifications.toggle()} notificationHelp={notifications.help} onAction={(action) => {
          if (action === "bugreport") {
            try { if (openNativeBugReport()) return; }
            catch (e) { toast(`Could not open the bug reporter: ${(e as Error).message}`, "err"); return; }
            setBugSession(selectedKey);
          }

          if (action === "launch") setLaunchGroup(undefined);
          setModal(action);
        }} />
      </header>

      <div className="rd-workspace">
      {/* The tower is a layer over the panes, not a replacement: terminals stay
          mounted at their size, since a remount replays output at a different
          width (B5). inert keeps keystrokes and focus out of the hidden panes. */}
      {towerOpen && !focusOpen && gridFolder === null && (
        <div className="rd-tower-layer">
          {analyticsTab ? <Analytics initialTab={analyticsTab} sessions={agents} onBack={() => setAnalyticsTab(null)} /> : <LiveControlTower
            onAnalytics={setAnalyticsTab}
            agents={agents}
            onBack={() => setTowerOpen(false)}
            onOpenTerminal={(key) => {
              setSelectedKey(key);
              setView("terminal");
              setTowerOpen(false);
            }}
          />}
        </div>
      )}
      <div className="rd-workspace-panes" {...(towerOpen && !focusOpen && gridFolder === null ? { inert: "" } : {})}>
      {focusOpen ? (
        <GridView key="focus" title="Focus" focus storageKey="rd.grid.focus"
          agents={pinnedSessions} folders={[]} themeFor={themeFor}
          onPin={toggleSessionPin} onSwitchFolder={() => undefined}
          onClose={() => setFocusOpen(false)} />
      ) : gridFolder !== null ? (
        <GridView
          key={gridFolder}
          storageKey={`rd.grid.folder.${gridFolder}`}
          title={gridFolder}
          themeFor={themeFor}
          agents={terminalAgents.filter(
            (s) =>
              s.group === gridFolder || s.group?.startsWith(gridFolder + "/"),
          )}
          folders={folders}
          onSwitchFolder={setGridFolder}
          onClose={() => setGridFolder(null)}
        />
      ) : (
        <div className={`rd-panels-3${selectedFolder !== null ? " rd-folder-selected" : ""}${sidePanels.collapsed.left ? " rd-agents-collapsed" : ""}${sidePanels.collapsed.right ? " rd-context-collapsed" : ""}`}>
          <section className={`rd-agents${sidePanels.collapsed.left ? " rd-side-collapsed" : ""}`}>
            <div className="rd-panel-head">
              <span>Agents</span>
              {(agents.length > 0 || folders.length > 0) && <SidebarFilterToggle filters={sidebarFilters.filters} expanded={sidebarFilters.expanded} onToggle={sidebarFilters.toggleExpanded} />}
              <PanelToggle side="left" collapsed={sidePanels.collapsed.left} onToggle={() => sidePanels.toggle("left")} />
            </div>
            {sessions.length === 0 && folders.length === 0 ? (
              <p className="rd-panel-empty">
                No agents yet. Choose New → New session to start one.
              </p>
            ) : (
              <LiveAgentTree
                filterControls={sidebarFilters}
                sessions={sessions}
                active={!towerOpen && !sidePanels.collapsed.left}
                folders={folders}
                selectedKey={selectedFolder === null ? selectedKey : null}
                selectedFolder={selectedFolder}
                onOpenFolder={setSelectedFolder}
                onFolderRenamed={(from, to) => setSelectedFolder(current => current === from ? to : current?.startsWith(from + "/") ? to + current.slice(from.length) : current)}
                onFolderDeleted={path => setSelectedFolder(current => current === path || current?.startsWith(path + "/") ? null : current)}
                onOpen={setSelectedKey}
                onOpenInbox={(key) => { setSelectedKey(key); setView("inbox"); }}
                onPin={toggleSessionPin}
                onFoldersChanged={refreshFolders}
                onSessionMoved={(key, group) =>
                  patchSession(key, { group: group || undefined })
                }
                onOpenGrid={setGridFolder}
                onOpenFolderInbox={setInboxFolder}
                onNewSessionIn={(folder) => {
                  setLaunchGroup(folder);
                  setModal("launch");
                }}
                folderThemes={themeOverrides.folders}
                onSetFolderTheme={setFolderTheme}
                termMode={mode}
              />
            )}
          </section>

          {selectedFolder !== null && <FolderView key={selectedFolder} folder={selectedFolder} onOpenSession={setSelectedKey} onGrid={() => setGridFolder(selectedFolder)} onMessage={() => setInboxFolder(selectedFolder)} onNewSession={() => { setLaunchGroup(selectedFolder); setModal("launch"); }} />}
          <section className="rd-terminal-pane" style={selectedFolder !== null ? { display: "none" } : undefined}>
            <div className="rd-view-toggle">
              <button
                className={view === "terminal" ? "active" : ""}
                onClick={() => setView("terminal")}
              >
                Terminal
              </button>
              <button
                className={view === "messages" ? "active" : ""}
                onClick={() => setView("messages")}
              >
                Messages
              </button>
              <button
                className={view === "history" ? "active" : ""}
                onClick={() => { setCheckpointTarget(null); setView("history"); }}
              >
                Timeline
              </button>
              <button
                className={view === "inbox" ? "active" : ""}
                onClick={() => setView("inbox")}
              >
                Inbox{selected && inboxCounts[selected.key] ? ` (${inboxCounts[selected.key]})` : ""}
              </button>
              <button className={view === "artifacts" ? "active" : ""} onClick={() => setView("artifacts")}>
                Artifacts
              </button>
              <span id="rd-shell-toggle" />
            </div>
            {selected && (view === "terminal" || view === "messages") && (
              <MessagePinStrip pins={messagePins.pins} error={messagePins.error} onOpen={(pin) => {
                setPinTarget({ sessionKey: selected.key, pin, request: ++pinSequence.current });
                setView("messages");
              }} />
            )}
            {/* Messages view: structured HTML render of the latest reply, with
              select-to-annotate. */}
            {view === "messages" && selected && (
              <div className="rd-messages-wrap">
                <Messages key={selected.key} sessionKey={selected.key} active={!towerOpen && selectedFolder === null}
                  pins={messagePins.pins} pinPending={messagePins.pending || !!messagePins.error}
                  onTogglePin={messagePins.toggle}
                  target={pinTarget?.sessionKey === selected.key ? pinTarget : null}
                  onClearTarget={() => setPinTarget(null)} />
              </div>
            )}
            {view === "history" && selected && (
              <div className="rd-messages-wrap">
                <TimelineView key={selected.key} session={selected} active={!towerOpen && selectedFolder === null} checkpointTarget={checkpointTarget?.key === selected.key ? checkpointTarget.request : undefined} onArtifacts={() => setView("artifacts")} />
              </div>
            )}
            {view === "inbox" && (
              <div className="rd-messages-wrap rd-inbox-wrap">
                {selected ? (
                  <LiveInboxView key={selected.key} session={selected} active={!towerOpen && selectedFolder === null} />
                ) : (
                  <p className="rd-panel-empty">Select a session to see its inbox.</p>
                )}
              </div>
            )}
            {view === "artifacts" && (
              <div className="rd-messages-wrap">
                {selected ? <ArtifactsView key={selected.key} sessionKey={selected.key} sessionName={selected.label} />
                  : <p className="rd-panel-empty">Select a session to see its artifacts.</p>}
              </div>
            )}
            {/* Keep recent views warm. Older browser views reconnect on demand;
              their server-owned terminals continue running while evicted. */}
            {mountedTerminals.map((s) => (
              <div
                key={s.key}
                data-key={s.key}
                className="rd-terminal-slot"
                style={{
                  display:
                    selectedFolder === null && view === "terminal" && s.key === selectedKey
                      ? "flex"
                      : "none",
                }}
              >
                <Terminal sessionKey={s.key} active={selectedFolder === null && view === "terminal" && s.key === selectedKey} theme={themeFor(s)} />
              </div>
            ))}
            {view === "terminal" && selected && !selected.ptyOwned && !selected.worktreePath && (
              <div className="rd-panel-empty">
                This agent isn’t running in a terminal Duckterm owns.
              </div>
            )}
            {selected && <SessionShell key={selected.key} session={selected} active={selectedFolder === null && view === "terminal" && !towerOpen} theme={themeFor(selected)} />}
            {view === "terminal" && !selected && (
              <div className="rd-panel-empty">
                Select an agent to see its terminal.
              </div>
            )}
          </section>

          <section className={`rd-context-pane${sidePanels.collapsed.right ? " rd-side-collapsed" : ""}`} style={selectedFolder !== null ? { display: "none" } : undefined}>
            <div className="rd-panel-head">
              <span>{selected ? selected.label : "Context"}</span>
              <PanelToggle side="right" collapsed={sidePanels.collapsed.right} onToggle={() => sidePanels.toggle("right")} />
            </div>
            <ContextViews session={<>
              {selected && <LiveSessionCard key={selected.key} session={selected} active={!towerOpen && selectedFolder === null && !sidePanels.collapsed.right}
                notesOpen={notesKey === selected.key} onCloseNotes={() => setNotesKey(null)} />}
              {selected && <LiveContextPanel session={selected} onCheckpointTimeline={() => { setCheckpointTarget(n => ({ key: selected.key, request: (n?.request ?? 0) + 1 })); setView("history"); }} active={!towerOpen && selectedFolder === null && !sidePanels.collapsed.right} />}
              {selected && selected.ptyOwned && (
                <label className="rd-session-theme">
                  terminal theme
                  <select
                    value={themeOverrides.sessions[selected.key] ?? ""}
                    onChange={(e) =>
                      setSessionTheme(selected.key, e.target.value || null)
                    }
                  >
                    <option value="">folder / {mode} default</option>
                    {themesForMode(mode).map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </>} connectors={<Connectors key={selected?.host ?? "local"} sessionKey={selected?.key} />} />
          </section>
        </div>
      )}
      </div>
      </div>

      {messageFolder !== null && <MessageFolderModal key={messageFolder} folder={messageFolder} onClose={() => setMessageFolder(null)} />}
      {inboxFolder !== null && messageFolder === null && (
        <Modal title={`${inboxFolder} · Interactions${desktop() ? " · This Mac" : ""}`} onClose={() => setInboxFolder(null)}>
          <InboxView key={inboxFolder} folder={inboxFolder} onMessageFolder={() => setMessageFolder(inboxFolder)} />
        </Modal>
      )}
      {modal === "launch" && (
        <LaunchModal
          group={launchGroup}
          folders={folders}
          onCreated={(key, group) => {
            setSelectedKey(key);
            window.dispatchEvent(new CustomEvent("reveal-sidebar-folder", { detail: group }));
            patchSession(key, { group: group || undefined });
            refreshFolders();
          }}
          onClose={() => {
            const native = desktop();
            if (native) delete native.draft;
            setModal(null);
            setLaunchGroup(undefined);
          }}
        />
      )}
      {modal === "agentsmd" && agentsMdDir && (
        <AgentsMdModal sessionKey={selected?.key} dir={agentsMdDir} onClose={() => setModal(null)} />
      )}
      {modal === "bugreport" && <BugReport session={bugSession} onClose={() => setModal(null)} />}
      {modal === "backup" && <BackupModal onClose={() => setModal(null)} />}
      {modal === "harnesses" && (
        <HarnessesModal
          defaultDir={agentsMdDir}
          sessionKey={selected?.key}
          onClose={() => setModal(null)}
        />
      )}
      {actionAnchor && actionSession && <SessionActions key={actionSession.key + ":" + actionAnchor.x + ":" + actionAnchor.y} anchor={actionAnchor} session={actionSession}
        onClose={closeActions} onFork={setForkKey} onDelete={deleteSession}
        onRename={(key, name) => patchSession(key, { label: name })}
        onNotes={key => { setSelectedKey(key); setSelectedFolder(null); setNotesKey(key); if (sidePanels.collapsed.right) sidePanels.toggle("right"); window.dispatchEvent(new Event("show-session-notes")); }}
        onUngroup={actionSession.group && !actionSession.parentKey ? async () => { await api.setGroup(actionSession.key, ""); patchSession(actionSession.key, { group: undefined }); refreshFolders(); } : undefined} />}
      {moveSession && <MoveRemoteModal session={moveSession} onClose={() => setMoveKey(null)} />}
      {forkSession && (
        <ForkModal session={forkSession} onClose={() => setForkKey(null)} />
      )}
      {modal === "folder" && (
        <NewFolderModal
          existing={folders}
          onClose={() => setModal(null)}
          onCreated={() => {
            refreshFolders();
            setModal(null);
          }}
        />
      )}
    </div>
  );
}

export function App() {
  return (
    <ToastProvider>
      <Dashboard />
    </ToastProvider>
  );
}
