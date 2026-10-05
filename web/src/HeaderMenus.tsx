import { useEffect, useRef, useState } from "react";
import { TermMode } from "./termThemes";
import { Theme } from "./useTheme";
import { SidebarDensity } from "./useSidebarDensity";
import "./headerMenus.css";
import { VoiceChoice, VoiceLevel } from "./voice";
import { LocalVoiceStatus } from "./api";


import { createPortal } from "react-dom";
import { SettingsPage, SettingsSection } from "./SettingsPage";

export type HeaderMenusProps = {
  density: SidebarDensity;
  onDensity: (density: SidebarDensity) => void;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  termMode: TermMode;
  termTheme: string;
  onTermTheme: (theme: string) => void;
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
  onAction: (action: "launch" | "folder" | "harnesses" | "backup" | "bugreport") => void;
  section: SettingsSection | null;
  onSection: (section: SettingsSection | null) => void;
  panels: { left: boolean; right: boolean };
  onPanel: (side: "left" | "right") => void;
  onFocus: () => void;
  hasFocus: boolean;
};
export function HeaderMenus(props: HeaderMenusProps) {
  const [open, setOpen] = useState(false);
  const [updateAvailable, setUpdateAvailable] = useState(() => { try { return localStorage.getItem("rd.updateAvailable") === "true"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem("rd.updateAvailable", String(updateAvailable)); } catch { /* Indicator still works without storage. */ } }, [updateAvailable]);
  const root = useRef<HTMLDivElement>(null), settingsButton = useRef<HTMLButtonElement>(null), newButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLElement>("#header-new-panel button")?.focus();
    const outside = (e: PointerEvent) => { if (e.target instanceof Node && !root.current?.contains(e.target)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); newButton.current?.focus(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  const target = document.getElementById("settings-page-root");
  return <div className="rd-header-menus" ref={root}>
    <button ref={settingsButton} className="rd-btn rd-settings-trigger" aria-pressed={props.section !== null} onClick={() => { setOpen(false); props.onSection(props.section ? null : "Appearance"); }}>Settings{updateAvailable && <span className="rd-update-dot" aria-label="Update available" />}</button>
    <div className="rd-header-dropdown rd-header-new">
      <button ref={newButton} className="rd-btn rd-btn-primary" aria-expanded={open} aria-controls="header-new-panel" onClick={() => setOpen(!open)} onKeyDown={e => { if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); } }}>New <span aria-hidden="true">▾</span></button>
      {open && <section id="header-new-panel" className="rd-header-popup" aria-label="New" onKeyDown={e => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
        e.preventDefault(); const buttons = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("button")], current = buttons.indexOf(document.activeElement as HTMLButtonElement);
        const index = e.key === "Home" ? 0 : e.key === "End" ? buttons.length - 1 : (current + (e.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length; buttons[index]?.focus();
      }}>
        <button className="rd-header-menu-item" onClick={() => { setOpen(false); props.onAction("launch"); }}>New session</button>
        <button className="rd-header-menu-item" onClick={() => { setOpen(false); props.onAction("folder"); }}>New folder</button>
      </section>}
    </div>
    {props.section && target && createPortal(<SettingsPage {...props} section={props.section} onUpdateAvailable={setUpdateAvailable} onClose={() => { props.onSection(null); settingsButton.current?.focus(); }} />, target)}
  </div>;
}
