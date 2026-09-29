import { useEffect, useMemo, useRef, useState } from "react";
import { useRelay } from "./relay";
import {
  Announcer,
  browserSpeaker,
  loadVoiceLevel,
  saveVoiceLevel,
  Speaker,
  VOICE_LEVELS,
  VoiceLevel,
  VoiceSession,
  VoiceTracker,
} from "./voice";

const TOAST_MS = 8000;

// Wires the tracker and announcer to live data: sessions from the dashboard,
// notes from the relay (polled only while voice is on), and keystrokes
// anywhere in the page, which hold announcements while the owner types.
export function useVoice(sessions: VoiceSession[], speaker: Speaker = browserSpeaker()) {
  const [level, setLevelState] = useState<VoiceLevel>(loadVoiceLevel);
  const [spoken, setSpoken] = useState<string | null>(null);
  const relay = useRelay(level !== "off");
  const typingAt = useRef(0);
  const tracker = useRef(new VoiceTracker());
  const [tick, setTick] = useState(0);
  const announcer = useMemo(() => {
    const shown: Speaker = {
      ...speaker,
      say: async (text) => {
        setSpoken(text);
        await speaker.say(text);
      },
    };
    return new Announcer(shown, () => typingAt.current);
    // The speaker is fixed for the page's life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const typed = () => { typingAt.current = Date.now(); };
    document.addEventListener("keydown", typed, true);
    const timer = setInterval(() => setTick((t) => t + 1), 5000); // completion holds and re-announcements
    return () => { document.removeEventListener("keydown", typed, true); clearInterval(timer); };
  }, []);

  useEffect(() => {
    announcer.add(tracker.current.update(sessions, relay.notes, level, Date.now()));
    // sessions is a fresh array each render; the key string captures what matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions.map((s) => `${s.key}:${s.state}`).join(","), relay.notes, level, tick]);

  useEffect(() => {
    if (!spoken) return;
    const timer = setTimeout(() => setSpoken(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [spoken]);

  function setLevel(next: VoiceLevel) {
    saveVoiceLevel(next);
    setLevelState(next);
    if (next === "off") {
      announcer.mute();
      setSpoken(null);
    } else if (level === "off") {
      // Browsers only allow speech after a user gesture; this click is one.
      void speaker.say("Voice announcements on");
    }
  }

  return { level, setLevel, spoken, dismiss: () => setSpoken(null), mute: () => { announcer.mute(); setSpoken(null); } };
}

export function VoiceMenu({ level, onLevel }: { level: VoiceLevel; onLevel: (level: VoiceLevel) => void }) {
  return (
    <label className="rd-voice-menu" title="Oracle voice announcements. They only play while a dashboard is open.">
      <span aria-hidden="true">{level === "off" ? "🔇" : "🔊"}</span>
      <select aria-label="Voice announcements" value={level} onChange={(e) => onLevel(e.target.value as VoiceLevel)}>
        {VOICE_LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
      </select>
    </label>
  );
}

// What was just said, with the two fixes one click away: drop to needs-you
// only, or mute.
export function VoiceToast({ text, level, onLevel, onMute, onDismiss }: {
  text: string;
  level: VoiceLevel;
  onLevel: (level: VoiceLevel) => void;
  onMute: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="rd-voice-toast" role="status">
      <span aria-hidden="true">🔊</span>
      <span className="rd-voice-toast-text">{text}</span>
      {(level === "done" || level === "all") && (
        <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => { onLevel("needs"); onDismiss(); }}>
          Only needs-you
        </button>
      )}
      <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onMute}>Stop</button>
      <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => onLevel("off")}>Turn off</button>
    </div>
  );
}
