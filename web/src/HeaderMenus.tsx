import type { Theme } from "./useTheme";
import { UpdateDuckTerm } from "./UpdateDuckTerm";
import { useEffect, useRef, useState } from "react";
import { SidebarDensity } from "./useSidebarDensity";
import "./headerMenus.css";
import { VOICE_LEVELS, VoiceChoice, VoiceLevel } from "./voice";
import { NaturalVoicePanel, VoicePicker } from "./VoiceControl";
import { LocalVoiceStatus } from "./api";

type Menu = "settings" | "new";
export function HeaderMenus({ theme, onTheme, density, onDensity, notifyOn, onNotify, notificationHelp, voiceLevel, onVoiceLevel, voice, onAction }: {
  theme?: Theme;
  onTheme?: (theme: Theme) => void;
  density: SidebarDensity;
  onDensity: (density: SidebarDensity) => void;
  notifyOn: boolean;
  onNotify: () => void;
  notificationHelp?: string;
  voiceLevel: VoiceLevel;
  onVoiceLevel: (level: VoiceLevel) => void;
  voice: {
    voices: VoiceChoice[];
    selected: string | null;
    ready: boolean;
    onSelect: (name: string) => void;
    onPreview: (name: string) => void;
    local: LocalVoiceStatus | null;
    fallbackReason: string | null;
    onInstall: () => void;
    onRemove: () => void;
  };
  onAction: (action: "launch" | "folder" | "harnesses" | "backup" | "bugreport" | "collaboration" | "checkpointing" | "appearance") => void;
}) {
  const [open, setOpen] = useState<Menu | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const settingsButton = useRef<HTMLButtonElement>(null);
  const newButton = useRef<HTMLButtonElement>(null);
  const notificationsAvailable = "Notification" in window;

  useEffect(() => {
    if (!open) return;
    const trigger = open === "new" ? newButton : settingsButton;
    const panel = root.current?.querySelector<HTMLElement>(`#header-${open}-panel`);
    panel?.querySelector<HTMLElement>("button, select, input")?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(null);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const action = (value: "launch" | "folder" | "harnesses" | "backup" | "bugreport" | "collaboration" | "checkpointing" | "appearance") => {
    setOpen(null);
    onAction(value);
  };
  return <div className="rd-header-menus" ref={root} onBlur={(event) => {
    if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) setOpen(null);
  }}>
    <div className="rd-header-dropdown">
      <button ref={settingsButton} className="rd-btn" aria-expanded={open === "settings"} aria-controls="header-settings-panel" onClick={() => setOpen(open === "settings" ? null : "settings")} onKeyDown={(event) => {
        if (event.key === "ArrowDown") { event.preventDefault(); setOpen("settings"); }
      }}>Settings <span aria-hidden="true">▾</span></button>
      {open === "settings" && <section id="header-settings-panel" className="rd-header-popup rd-settings-popup" aria-label="Settings">
        <button className="rd-header-menu-item" onClick={() => action("appearance")}>Text sizes <span aria-hidden="true">›</span></button>
        {onTheme && <label className="rd-header-menu-field">App theme<select value={theme} onChange={e => onTheme(e.target.value as Theme)}><option value="system">Follow system</option><option value="dark">Dark</option><option value="light">Light</option></select></label>}
        <label className="rd-header-menu-field">Sidebar density<select value={density} onChange={(event) => onDensity(event.target.value as SidebarDensity)}>
          <option value="compact">Compact</option><option value="standard">Standard</option><option value="relaxed">Relaxed</option>
        </select></label>
        <div className="rd-header-menu-divider" />
        <div className="rd-header-notifications">
          <label>Desktop notifications<input type="checkbox" checked={notifyOn} disabled={!notificationsAvailable} onChange={onNotify} aria-describedby="header-notification-help" /></label>
          <p id="header-notification-help">{notificationHelp ?? (notificationsAvailable ? "Notify when an agent needs an answer." : "Desktop notifications are unavailable in this browser.")}</p>
        </div>
        <div className="rd-header-notifications">
          <label>Voice announcements<select value={voice.ready ? voiceLevel : "off"} disabled={!voice.ready} onChange={(event) => onVoiceLevel(event.target.value as VoiceLevel)} aria-describedby="header-voice-help">
            {VOICE_LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select></label>
          <p id="header-voice-help">
            {voice.ready
              ? "Oracle says when an agent needs your input or finishes. It only speaks while a dashboard is open."
              : "Download a voice below to turn voice announcements on."}
          </p>
          <VoicePicker voices={voice.voices} selected={voice.selected} onSelect={voice.onSelect} onPreview={voice.onPreview} />
          <NaturalVoicePanel status={voice.local} fallbackReason={voice.fallbackReason} onInstall={voice.onInstall} onRemove={voice.onRemove} />
        </div>
        <div className="rd-header-menu-divider" />
        <button className="rd-header-menu-item" onClick={() => action("collaboration")}>Collaboration <span aria-hidden="true">›</span></button>
        <button className="rd-header-menu-item" onClick={() => action("checkpointing")}>Checkpointing <span aria-hidden="true">›</span></button>
        <button className="rd-header-menu-item" onClick={() => action("bugreport")}>Report a bug <span aria-hidden="true">›</span></button>
        <button className="rd-header-menu-item" onClick={() => action("backup")}>Back up to remote <span aria-hidden="true">›</span></button>
        <button className="rd-header-menu-item" title="Manage skill kits for your projects" onClick={() => action("harnesses")}>Harnesses <span aria-hidden="true">›</span></button>
        <div className="rd-header-menu-divider" /><UpdateDuckTerm />
      </section>}
    </div>
    <div className="rd-header-dropdown rd-header-new">
      <button ref={newButton} className="rd-btn rd-btn-primary" aria-expanded={open === "new"} aria-controls="header-new-panel" onClick={() => setOpen(open === "new" ? null : "new")} onKeyDown={(event) => {
        if (event.key === "ArrowDown") { event.preventDefault(); setOpen("new"); }
      }}>New <span aria-hidden="true">▾</span></button>
      {open === "new" && <section id="header-new-panel" className="rd-header-popup" aria-label="New" onKeyDown={(event) => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button")];
        const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
        const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (current + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
        buttons[index]?.focus();
      }}>
        <button className="rd-header-menu-item" onClick={() => action("launch")}>New session</button>
        <button className="rd-header-menu-item" onClick={() => action("folder")}>New folder</button>
      </section>}
    </div>
  </div>;
}
