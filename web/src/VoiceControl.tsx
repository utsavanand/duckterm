import { useEffect, useMemo, useRef, useState } from "react";
import { useRelay } from "./relay";
import {
  announce,
  Announcer,
  browserSpeaker,
  defaultVoice,
  hasQualityVoice,
  loadVoiceLevel,
  loadVoiceName,
  saveVoiceLevel,
  saveVoiceName,
  usableVoices,
  VoiceChoice,
  Speaker,
  VOICE_LEVELS,
  VoiceLevel,
  VoiceSession,
  VoiceSnapshot,
} from "./voice";

const TOAST_MS = 8000;
export const PREVIEW_LINE = "architect needs your input";

// speechSynthesis fills its voice list asynchronously in some browsers, so the
// first call can be empty; voiceschanged delivers the real list.
export function useSpeechVoices(): VoiceChoice[] {
  // Some embedded webviews have speak() without a voice list.
  const [voices, setVoices] = useState<VoiceChoice[]>(() =>
    "speechSynthesis" in window ? window.speechSynthesis.getVoices?.() ?? [] : [],
  );
  useEffect(() => {
    const synth = "speechSynthesis" in window ? window.speechSynthesis : undefined;
    if (!synth) return;
    const load = () => setVoices(synth.getVoices?.() ?? []);
    load();
    synth.addEventListener?.("voiceschanged", load);
    return () => synth.removeEventListener?.("voiceschanged", load);
  }, []);
  return voices;
}

// Browsers refuse speech until the page has had a click or keypress, so after
// a reload voice is on but silent. Say so instead of looking broken.
function needsGesture(): boolean {
  const activation = (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation;
  return activation ? !activation.hasBeenActive : false;
}

// Wires the pure diff and the announcer to live data: sessions from the
// dashboard, notes from the relay (polled only while voice is on), and
// keystrokes into a terminal, which hold announcements while the owner types.
export function useVoice(sessions: VoiceSession[], speaker: Speaker = browserSpeaker()) {
  const [level, setLevelState] = useState<VoiceLevel>(loadVoiceLevel);
  const [spoken, setSpoken] = useState<string | null>(null);
  const [paused, setPaused] = useState(() => level !== "off" && needsGesture());
  const relay = useRelay(level !== "off");
  const typingAt = useRef(0);
  const previous = useRef<VoiceSnapshot | null>(null);
  const [tick, setTick] = useState(0);
  const announcer = useMemo(() => {
    const shown: Speaker = {
      ...speaker,
      say: async (text) => {
        setSpoken(text);
        const spoke = await speaker.say(text);
        if (spoke === false) {
          setSpoken(null);
          setPaused(true);
        }
        return spoke;
      },
    };
    return new Announcer(shown, () => typingAt.current);
    // The speaker is fixed for the page's life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const typed = (e: KeyboardEvent) => {
      if (e.target instanceof Element && e.target.closest(".xterm")) typingAt.current = Date.now();
    };
    const resume = () => setPaused(false);
    document.addEventListener("keydown", typed, true);
    document.addEventListener("pointerdown", resume, true);
    document.addEventListener("keydown", resume, true);
    const timer = setInterval(() => setTick((t) => t + 1), 5000); // the 15-minute boundary
    return () => {
      document.removeEventListener("keydown", typed, true);
      document.removeEventListener("pointerdown", resume, true);
      document.removeEventListener("keydown", resume, true);
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const next: VoiceSnapshot = { at: Date.now(), sessions, notes: relay.loaded ? relay.notes : null };
    announcer.add(announce(previous.current, next, level));
    previous.current = next;
    // sessions is a fresh array each render; the key string captures what matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions.map((s) => `${s.key}:${s.state}:${s.waitingSince ?? ""}`).join(","), relay.notes, relay.loaded, level, tick]);

  useEffect(() => {
    if (!spoken) return;
    const timer = setTimeout(() => setSpoken(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [spoken]);

  function setLevel(next: VoiceLevel) {
    saveVoiceLevel(next);
    setLevelState(next);
    setPaused(false); // this change came from a click or key, which unlocks speech
    if (next === "off") {
      announcer.mute();
      setSpoken(null);
    } else if (level === "off") {
      void speaker.say("Voice announcements on");
    }
  }

  const allVoices = useSpeechVoices();
  const [voiceName, setVoiceNameState] = useState<string | null>(loadVoiceName);
  const language = navigator.language || "en-US";
  const voices = usableVoices(allVoices, language);
  const selectedVoice = voices.find((v) => v.name === voiceName)?.name ?? defaultVoice(allVoices, language)?.name ?? null;

  return {
    level,
    setLevel,
    voices,
    selectedVoice,
    qualityVoices: hasQualityVoice(allVoices, language),
    setVoice: (name: string) => {
      saveVoiceName(name);
      setVoiceNameState(name);
    },
    preview: (name: string) => {
      speaker.stop();
      void speaker.say(PREVIEW_LINE, name);
    },
    spoken,
    paused: paused && level !== "off",
    dismiss: () => setSpoken(null),
    mute: () => { announcer.mute(); setSpoken(null); },
  };
}

// Listen before choosing: each voice has its own Preview.
export function VoicePicker({ voices, selected, qualityVoices, onSelect, onPreview }: {
  voices: VoiceChoice[];
  selected: string | null;
  qualityVoices: boolean;
  onSelect: (name: string) => void;
  onPreview: (name: string) => void;
}) {
  return (
    <div className="rd-voice-picker">
      <div className="rd-voice-picker-label" id="voice-picker-label">Voice</div>
      {voices.length === 0 ? (
        <p className="rd-voice-picker-hint">No voices found for your language yet.</p>
      ) : (
        <div className="rd-voice-picker-list" role="radiogroup" aria-labelledby="voice-picker-label">
          {voices.map((v) => (
            <div key={v.name} className="rd-voice-picker-row">
              <label>
                <input type="radio" name="rd-voice-name" checked={v.name === selected} onChange={() => onSelect(v.name)} />
                {v.name}
              </label>
              <button type="button" className="rd-btn rd-btn-ghost rd-btn-sm" aria-label={`Preview ${v.name}`} onClick={() => onPreview(v.name)}>
                Preview
              </button>
            </div>
          ))}
        </div>
      )}
      {!qualityVoices && (
        <p className="rd-voice-picker-hint">
          For a more natural voice, download an Enhanced or Premium voice for free: System Settings, Accessibility, Spoken Content, then the System voice menu, Manage Voices. Reload DuckTerm afterwards.
        </p>
      )}
    </div>
  );
}

export function VoicePausedPill() {
  return (
    <div className="rd-voice-toast rd-voice-paused" role="status">
      <span aria-hidden="true">🔇</span>
      <span>Voice paused. Click anywhere to resume.</span>
    </div>
  );
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
