import { ReactNode, useEffect, useRef } from "react";
import type { HeaderMenusProps } from "./HeaderMenus";
import { AUTO, themesForMode } from "./termThemes";
import { NaturalVoicePanel, VoicePicker } from "./VoiceControl";
import { VOICE_LEVELS, VoiceLevel } from "./voice";
import { UpdateDuckTerm } from "./UpdateDuckTerm";
import { desktop } from "./desktop";
import { useToast } from "./ui";
import "./settingsPage.css";

export const SETTINGS_SECTIONS = ["Appearance", "Notifications & voice", "Backup", "Capabilities", "Updates", "Report a bug"] as const;
export type SettingsSection = typeof SETTINGS_SECTIONS[number];
export const DOCS_URL = "https://github.com/utsavanand/duckterm#readme";
export async function copyDiagnostics(): Promise<void> {
  // Deliberately exclude session content, directories, host names and credentials.
  const content = `DuckTerm dashboard\nSurface: ${desktop() ? "Mac app" : "Browser"}\nAppearance: ${document.documentElement.dataset.theme ?? "system"}\nOnline: ${navigator.onLine ? "yes" : "no"}\n`;
  const bridge = window.webkit?.messageHandlers?.remoteSession;
  if (desktop()?.canSettingsMenu && bridge) { bridge.postMessage({ action: "copy-diagnostics", text: content }); return; }
  await navigator.clipboard.writeText(content);
}
function Group({ title, children }: { title: string; children: ReactNode }) {
  return <section className="rd-settings-group"><h2>{title}</h2>{children}</section>;
}
function Row({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <div className="rd-settings-row"><div><h3>{title}</h3><p>{description}</p></div><div className="rd-settings-controls">{children}</div></div>;
}
export function SettingsPage(p: HeaderMenusProps & { section: SettingsSection; onClose: () => void; onUpdateAvailable: (available: boolean) => void }) {
  const title = useRef<HTMLHeadingElement>(null), toast = useToast();
  const onClose = p.onClose;
  useEffect(() => { title.current?.focus(); }, [p.section]);
  useEffect(() => {
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector('[role="dialog"]')) { e.preventDefault(); onClose(); } };
    window.addEventListener("keydown", escape); return () => window.removeEventListener("keydown", escape);
  }, [onClose]);
  const descriptions: Record<SettingsSection, string> = {
    Appearance: "Make DuckTerm comfortable to read and work in.", "Notifications & voice": "Choose when you hear from your agents, and how they sound.",
    Backup: "Keep a recoverable copy of your DuckTerm workspace.", Capabilities: "Manage reusable capabilities for your projects.",
    Updates: "Keep DuckTerm up to date.", "Report a bug": "Describe the problem and review what you include before sharing.",
  };
  return <section className="rd-settings-page" aria-label="Settings">
    <nav className="rd-settings-nav" aria-label="Settings sections">
      <button className="rd-btn rd-btn-ghost" onClick={p.onClose}>← Sessions</button><span className="rd-settings-caption">Settings</span>
      {SETTINGS_SECTIONS.map(section => <button key={section} aria-current={section === p.section ? "page" : undefined} className="rd-settings-tab" onClick={() => p.onSection(section)}>{section}</button>)}
      <a className="rd-settings-docs" href={DOCS_URL} target="_blank" rel="noreferrer">DuckTerm docs ↗</a>
    </nav>
    <div className="rd-settings-content"><h1 tabIndex={-1} ref={title}>{p.section}</h1><p className="rd-settings-intro">{descriptions[p.section]}</p>
      {p.section === "Appearance" && <>
        <Group title="Theme"><Row title="App appearance" description="Use your system setting or choose a fixed appearance."><div className="rd-settings-theme" role="group" aria-label="Theme">{(["system", "light", "dark"] as const).map(theme => <button key={theme} className="rd-btn" aria-pressed={p.theme === theme} onClick={() => p.onTheme(theme)}>{theme[0].toUpperCase() + theme.slice(1)}</button>)}</div></Row>
          <Row title="Terminal colors" description="Automatic follows your app appearance."><select className="rd-term-theme" aria-label="Terminal colors" value={p.termTheme} onChange={e => p.onTermTheme(e.target.value)}><option value={AUTO}>Automatic ({p.termMode})</option>{themesForMode(p.termMode).map(t => <option key={t} value={t}>{t}</option>)}</select></Row>
        </Group>
        <Group title="Layout"><Row title="Sidebar density" description="Compact shows essentials; Relaxed keeps details and actions visible."><select aria-label="Sidebar density" value={p.density} onChange={e => p.onDensity(e.target.value as HeaderMenusProps["density"])}><option value="compact">Compact</option><option value="standard">Standard</option><option value="relaxed">Relaxed</option></select></Row>
          <Row title="Panels" description="Adjust the space available for sessions and their details."><button className="rd-btn" onClick={() => p.onPanel("left")}>{p.panels.left ? "Show" : "Hide"} sidebar</button><button className="rd-btn" onClick={() => p.onPanel("right")}>{p.panels.right ? "Show" : "Hide"} right panel</button></Row>
          <Row title="Focus" description="Keep pinned sessions together in one view."><button className="rd-btn" disabled={!p.hasFocus} onClick={p.onFocus}>Open Focus</button></Row></Group>
        <p className="rd-settings-help">Changes apply immediately. Your current terminal and draft stay open.</p>
      </>}
      {p.section === "Notifications & voice" && <>
        <Group title="When to notify"><Row title="Desktop notifications" description={p.notificationHelp ?? "Notify when an agent needs an answer."}><input type="checkbox" aria-label="Desktop notifications" checked={p.notifyOn} disabled={!("Notification" in window)} onChange={p.onNotify} /></Row>
          <Row title="Voice announcements" description={p.voice.ready ? "Announce updates while a dashboard is open." : "Download a voice below to turn announcements on."}><select aria-label="Voice announcements" value={p.voice.ready ? p.voiceLevel : "off"} disabled={!p.voice.ready} onChange={e => p.onVoiceLevel(e.target.value as VoiceLevel)}>{VOICE_LEVELS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}</select></Row></Group>
        <Group title="Voice"><div className="rd-settings-voice"><VoicePicker voices={p.voice.voices} selected={p.voice.selected} onSelect={p.voice.onSelect} onPreview={p.voice.onPreview} /><NaturalVoicePanel status={p.voice.local} fallbackReason={p.voice.fallbackReason} onInstall={p.voice.onInstall} onRemove={p.voice.onRemove} /></div></Group>
      </>}
      {p.section === "Backup" && <Group title="Remote backup"><Row title="Destination and backups" description="Choose a destination, follow progress and review the last completed backup."><button className="rd-btn rd-btn-primary" onClick={() => p.onAction("backup")}>Back up to remote</button></Row></Group>}
      {p.section === "Capabilities" && <><Group title="Meta-harnesses"><Row title="Registered meta-harnesses" description="Manage the configurations already available to your projects."><button className="rd-btn" onClick={() => p.onAction("harnesses")}>Manage meta-harnesses</button></Row></Group><Group title="Skills and collections"><Row title="Individual skill" description="Adding individual skills will arrive with meta-harness support."><button className="rd-btn" disabled>Add skill</button></Row><Row title="Meta-harness" description="Collections of skills, hooks and instructions will be available together."><button className="rd-btn" disabled>Add meta-harness</button></Row></Group></>}
      {p.section === "Updates" && <div className="rd-settings-group rd-settings-update"><UpdateDuckTerm onAvailable={p.onUpdateAvailable} /></div>}
      {p.section === "Report a bug" && <><Group title="Prepare a report"><Row title="Report a bug" description="Review diagnostic context and attachments before creating an email draft."><button className="rd-btn rd-btn-primary" onClick={() => p.onAction("bugreport")}>Prepare report</button></Row><Row title="Basic diagnostics" description="Copies the app surface, appearance and online status. No session content, paths or credentials."><button className="rd-btn" onClick={() => void copyDiagnostics().then(() => toast("Diagnostics copied")).catch(() => toast("Could not copy diagnostics. Check clipboard access.", "err"))}>Copy diagnostics</button></Row></Group><p className="rd-settings-help">{desktop()?.canReportBug ? "Opens the existing Mac report window, also available from Help." : "Prepare a reviewed email draft or download a ZIP in the dashboard report form."}</p></>}
    </div>
  </section>;
}
