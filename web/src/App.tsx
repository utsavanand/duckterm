import { useEffect, useMemo, useRef, useState } from "react";
import { AgentsMdModal } from "./AgentsMdModal";
import { AgentTree } from "./AgentTree";
import { api } from "./api";
import { Approvals } from "./Approvals";
import { ContextPanel } from "./ContextPanel";
import { ForkModal } from "./ForkModal";
import { GridView } from "./GridView";
import { HarnessesModal } from "./HarnessesModal";
import { LaunchModal } from "./LaunchModal";
import { Messages } from "./Messages";
import { NewFolderModal } from "./NewFolderModal";
import { Terminal } from "./Terminal";
import { effectiveState } from "./sessions";
import { TERM_THEMES, loadTermTheme } from "./termThemes";
import { ToastProvider, useToast } from "./ui";
import { useEventStream } from "./useEventStream";
import { useTheme } from "./useTheme";

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function Dashboard() {
  const { sessions, connected, removeSessions, patchSession } =
    useEventStream();
  const toast = useToast();
  const now = useNow(1000);
  const { theme, cycle: cycleTheme } = useTheme();

  const [modal, setModal] = useState<
    "launch" | "agentsmd" | "folder" | "harnesses" | null
  >(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [forkKey, setForkKey] = useState<string | null>(null);
  const [view, setView] = useState<"terminal" | "messages">("terminal");
  // The folder whose terminals are tiled fullscreen; null = grid closed.
  const [gridFolder, setGridFolder] = useState<string | null>(null);
  // Terminal color theme — applies live to every mounted terminal.
  const [termTheme, setTermTheme] = useState<string>(loadTermTheme);
  useEffect(() => {
    localStorage.setItem("rd-term-theme", termTheme);
  }, [termTheme]);

  // Folders persist on the server (incl. empty ones); the left list groups by
  // them. Refetch when sessions change, since moving a session can create or
  // clear a folder.
  const [folders, setFolders] = useState<string[]>([]);
  const refreshFolders = () =>
    api
      .folders()
      .then((d) => setFolders(d.folders))
      .catch(() => undefined);
  useEffect(() => {
    refreshFolders();
  }, [sessions.length]);

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

  // Every agent runs inside Duckterm now — one flat list, no lifecycle/origin
  // filters. Terminated sessions stay out of the live list.
  const agents = useMemo(
    () => sessions.filter((s) => effectiveState(s, now) !== "archived"),
    [sessions, now],
  );

  // "Needs you": the whole point of a fleet view is not staring at it. The
  // count rides the tab title; sessions that ENTER waiting fire a browser
  // notification (if the user granted permission via the bell).
  const waiting = useMemo(
    () => sessions.filter((s) => effectiveState(s, now) === "waiting"),
    [sessions, now],
  );
  const waitingKeys = waiting.map((s) => s.key).join(",");
  const prevWaiting = useRef<Set<string>>(new Set());
  const [notifyOn, setNotifyOn] = useState(
    () => "Notification" in window && Notification.permission === "granted",
  );
  useEffect(() => {
    document.title = waiting.length
      ? `(${waiting.length}) RubberTerm`
      : "RubberTerm";
    const current = new Set(waiting.map((s) => s.key));
    if (notifyOn) {
      for (const s of waiting) {
        if (!prevWaiting.current.has(s.key)) {
          new Notification(`${s.label} needs you`, {
            body: "The agent is waiting on an answer.",
          });
        }
      }
    }
    prevWaiting.current = current;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waitingKeys, notifyOn]);

  async function toggleNotify() {
    if (!("Notification" in window)) return;
    if (Notification.permission !== "granted") {
      const perm = await Notification.requestPermission();
      setNotifyOn(perm === "granted");
    } else {
      setNotifyOn((v) => !v);
    }
  }

  // Default the selection to the first agent so the center pane isn't empty.
  useEffect(() => {
    if (selectedKey && sessions.some((s) => s.key === selectedKey)) return;
    setSelectedKey(agents[0]?.key ?? null);
  }, [agents, selectedKey, sessions]);

  const selected = sessions.find((s) => s.key === selectedKey) ?? null;
  const forkSession = sessions.find((s) => s.key === forkKey) ?? null;
  // Agents whose terminal we keep mounted (PTY Duckterm owns). Switching
  // between them is then instant — no WS reconnect, no buffer replay.
  const terminalAgents = useMemo(
    () => agents.filter((s) => s.ptyOwned),
    [agents],
  );

  // Terminals stay mounted, so switching agents only flips which slot is shown —
  // the Terminal's own mount-focus doesn't fire. Focus the newly-visible slot's
  // input so you can type into it right after switching.
  useEffect(() => {
    if (!selectedKey) return;
    const focus = () => {
      const ta = document.querySelector<HTMLTextAreaElement>(
        `.rd-terminal-slot[data-key="${selectedKey}"] .xterm-helper-textarea`,
      );
      ta?.focus();
    };
    const t = setTimeout(focus, 0);
    return () => clearTimeout(t);
  }, [selectedKey]);

  const labels = useMemo(
    () => Object.fromEntries(sessions.map((s) => [s.key, s.label])),
    [sessions],
  );
  const knownKeys = useMemo(
    () => new Set(sessions.map((s) => s.key)),
    [sessions],
  );
  // The selected agent's working directory anchors AGENTS.md (per-folder file).
  const agentsMdDir = selected?.worktreePath ?? selected?.cwd ?? null;

  return (
    <div className="rd-app">
      <header className="rd-topbar">
        <span className="rd-brand">
          <img
            className="rd-brand-mark"
            src="/favicon.svg"
            alt=""
            width={22}
            height={22}
          />
          Rubber<span className="rd-brand-term">Term</span>
        </span>
        <span className="rd-live">
          <span className={`dot ${connected ? "on" : "off"}`} />
          {connected ? "Live" : "Disconnected"}
        </span>
        <span className="rd-spacer" />
        <button
          className="rd-btn rd-btn-ghost rd-btn-sm"
          onClick={() => setModal("agentsmd")}
          disabled={!agentsMdDir}
          title={
            agentsMdDir
              ? "Edit the AGENTS.md for this agent's folder"
              : "Select an agent to edit its AGENTS.md"
          }
        >
          AGENTS.md
        </button>
        <button
          className="rd-btn rd-btn-ghost rd-btn-sm"
          title={
            notifyOn
              ? "Desktop notifications on (click to mute)"
              : "Notify me when an agent needs an answer"
          }
          onClick={toggleNotify}
          aria-label="Toggle notifications"
        >
          {notifyOn ? "🔔" : "🔕"}
        </button>
        <select
          className="rd-term-theme"
          title="Terminal color theme"
          value={termTheme}
          onChange={(e) => setTermTheme(e.target.value)}
        >
          {Object.keys(TERM_THEMES).map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <button
          className="rd-btn rd-btn-ghost rd-btn-sm"
          title={`Theme: ${theme} (click to change)`}
          onClick={cycleTheme}
          aria-label="Toggle theme"
        >
          {theme === "light" ? "☀︎" : theme === "dark" ? "☾" : "◐"}
        </button>
        <button
          className="rd-btn rd-btn-ghost rd-btn-sm"
          onClick={() => setModal("harnesses")}
          title="Install suites of skills/hooks/sub-agents (like uv-suite) into a project"
        >
          Harnesses
        </button>
        <button
          className="rd-btn rd-btn-ghost rd-btn-sm"
          onClick={() => setModal("folder")}
          title="Create a folder to group agents"
        >
          New folder
        </button>
        <button
          className="rd-btn rd-btn-primary"
          onClick={() => setModal("launch")}
        >
          New session
        </button>
      </header>

      {gridFolder !== null ? (
        <GridView
          key={gridFolder}
          title={gridFolder}
          termTheme={termTheme}
          agents={terminalAgents.filter(
            (s) =>
              s.group === gridFolder ||
              s.group?.startsWith(gridFolder + "/"),
          )}
          folders={folders}
          onSwitchFolder={setGridFolder}
          onClose={() => setGridFolder(null)}
        />
      ) : (
      <div className="rd-panels-3">
        <section className="rd-agents">
          <div className="rd-panel-head">
            <span>Agents</span>
          </div>
          {agents.length === 0 ? (
            <p className="rd-panel-empty">
              No agents yet. Click New session to start one.
            </p>
          ) : (
            <AgentTree
              sessions={agents}
              now={now}
              labels={labels}
              folders={folders}
              onOpen={setSelectedKey}
              onFork={setForkKey}
              onDelete={deleteSession}
              onFoldersChanged={refreshFolders}
              onSessionMoved={(key, group) =>
                patchSession(key, { group: group || undefined })
              }
              onRename={(key, name) => patchSession(key, { label: name })}
              onOpenGrid={setGridFolder}
            />
          )}
        </section>

        <section className="rd-terminal-pane">
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
          </div>
          {/* Messages view: structured HTML render of the latest reply, with
              select-to-annotate. */}
          {view === "messages" && selected && (
            <div className="rd-messages-wrap">
              <Messages sessionKey={selected.key} />
            </div>
          )}
          {/* Terminal view: keep a terminal MOUNTED per PTY-owned agent and just
              show the selected one. Re-mounting on every switch would reconnect
              the WS and replay the whole buffer from scratch each time. */}
          {terminalAgents.map((s) => (
            <div
              key={s.key}
              data-key={s.key}
              className="rd-terminal-slot"
              style={{
                display:
                  view === "terminal" && s.key === selectedKey
                    ? "flex"
                    : "none",
              }}
            >
              <Terminal sessionKey={s.key} theme={termTheme} />
            </div>
          ))}
          {selected && !selected.ptyOwned && !selected.worktreePath && (
            <div className="rd-panel-empty">
              This agent isn’t running in a terminal Duckterm owns.
            </div>
          )}
          {!selected && (
            <div className="rd-panel-empty">
              Select an agent to see its terminal.
            </div>
          )}
        </section>

        <section className="rd-context-pane">
          <div className="rd-panel-head">
            <span>{selected ? selected.label : "Context"}</span>
          </div>
          <div className="rd-context-body">
            <Approvals
              labels={labels}
              pollKey={sessions.length}
              onOpen={setSelectedKey}
              knownKeys={knownKeys}
              waiting={waiting}
            />
            {selected && <ContextPanel session={selected} />}
          </div>
        </section>
      </div>
      )}

      {modal === "launch" && <LaunchModal onClose={() => setModal(null)} />}
      {modal === "agentsmd" && agentsMdDir && (
        <AgentsMdModal dir={agentsMdDir} onClose={() => setModal(null)} />
      )}
      {modal === "harnesses" && (
        <HarnessesModal
          defaultDir={agentsMdDir}
          onClose={() => setModal(null)}
        />
      )}
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
