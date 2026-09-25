import { useEffect, useRef, useState } from "react";
import { AUTO, TermMode, themesForMode } from "./termThemes";
import { Theme } from "./useTheme";
import { SidebarDensity } from "./useSidebarDensity";
import "./headerMenus.css";

type Menu = "settings" | "new";
export function HeaderMenus({ density, onDensity, theme, onTheme, termMode, termTheme, onTermTheme, notifyOn, onNotify, onAction }: {
  density: SidebarDensity;
  onDensity: (density: SidebarDensity) => void;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  termMode: TermMode;
  termTheme: string;
  onTermTheme: (theme: string) => void;
  notifyOn: boolean;
  onNotify: () => void;
  onAction: (action: "launch" | "folder" | "harnesses" | "backup") => void;
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

  const action = (value: "launch" | "folder" | "harnesses" | "backup") => {
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
        <div className="rd-header-menu-group">Appearance</div>
        <label className="rd-header-menu-field">Theme<select value={theme} onChange={(event) => onTheme(event.target.value as Theme)}>
          <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
        </select></label>
        <label className="rd-header-menu-field">Terminal colors<select className="rd-term-theme" title={`Terminal color theme (${termMode} mode)`} value={termTheme} onChange={(event) => onTermTheme(event.target.value)}>
          <option value={AUTO}>Auto ({termMode})</option>
          {themesForMode(termMode).map((name) => <option key={name} value={name}>{name}</option>)}
        </select></label>
        <label className="rd-header-menu-field">Sidebar density<select value={density} onChange={(event) => onDensity(event.target.value as SidebarDensity)}>
          <option value="compact">Compact</option><option value="standard">Standard</option><option value="relaxed">Relaxed</option>
        </select></label>
        <div className="rd-header-menu-divider" />
        <div className="rd-header-notifications">
          <label>Desktop notifications<input type="checkbox" checked={notifyOn} disabled={!notificationsAvailable} onChange={onNotify} aria-describedby="header-notification-help" /></label>
          <p id="header-notification-help">{notificationsAvailable ? "Notify when an agent needs an answer." : "Desktop notifications are unavailable in this browser."}</p>
        </div>
        <div className="rd-header-menu-divider" />
        <button className="rd-header-menu-item" onClick={() => action("backup")}>Back up to remote <span aria-hidden="true">›</span></button>
        <button className="rd-header-menu-item" title="Manage meta-harnesses for your projects" onClick={() => action("harnesses")}>Harnesses <span aria-hidden="true">›</span></button>
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
