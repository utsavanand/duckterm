import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, LocalVoiceStatus } from "./api";
import { useRelay } from "./relay";
import {
  announce,
  Announcer,
  audioPlayer,
  chimePlayer,
  chosenVoice,
  naturalChoices,
  naturalSpeaker,
  loadVoiceLevel,
  loadVoiceName,
  saveVoiceLevel,
  saveVoiceName,
  VoiceChoice,
  Speaker,
  VOICE_LEVELS,
  VoiceLevel,
  VoiceSession,
  VoiceSnapshot,
} from "./voice";

const TOAST_MS = 8000;
export const PREVIEW_LINE = "architect needs your input";

// The optional local Kokoro voice: its status, polled quickly while installing.
export function useLocalVoice() {
  const [status, setStatus] = useState<LocalVoiceStatus | null>(null);
  const refresh = useCallback(() => {
    api.voiceStatus().then(setStatus).catch(() => setStatus(null));
  }, []);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, status?.state === "installing" ? 2000 : 60_000);
    return () => clearInterval(timer);
  }, [refresh, status?.state]);
  return {
    status,
    install: () => { api.voiceInstall().then(setStatus).catch(() => refresh()); },
    remove: () => { api.voiceRemove().then(setStatus).catch(() => refresh()); },
  };
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
export function useVoice(sessions: VoiceSession[], injected?: Speaker) {
  const [level, setLevelState] = useState<VoiceLevel>(loadVoiceLevel);
  const [fallbackReason, setFallbackReason] = useState<string | null>(null);
  const local = useLocalVoice();
  const chosenRef = useRef<string | null>(null);
  const speaker = useMemo(() => {
    if (injected) return injected;
    const player = audioPlayer();
    return naturalSpeaker({
      fetchWav: api.voiceSay,
      play: player.play,
      stopPlayback: player.stop,
      chime: chimePlayer(),
      chosen: () => chosenRef.current,
      onFallback: setFallbackReason,
    });
    // Built once for the page's life; it reads the current choice through a ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Voice speaks only in a natural voice, so it stays off until one is
  // installed; the owner's chosen level is kept for when it is.
  const ready = local.status?.state === "ready";
  const active: VoiceLevel = ready ? level : "off";
  const [spoken, setSpoken] = useState<string | null>(null);
  const [paused, setPaused] = useState(() => level !== "off" && needsGesture());
  const relay = useRelay(active !== "off");
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
    announcer.add(announce(previous.current, next, active));
    previous.current = next;
    // sessions is a fresh array each render; the key string captures what matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions.map((s) => `${s.key}:${s.state}:${s.waitingSince ?? ""}`).join(","), relay.notes, relay.loaded, active, tick]);

  useEffect(() => {
    if (!spoken) return;
    const timer = setTimeout(() => setSpoken(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [spoken]);

  function setLevel(next: VoiceLevel) {
    if (!ready && next !== "off") return; // nothing to speak with yet
    saveVoiceLevel(next);
    setLevelState(next);
    setPaused(false); // this change came from a click or key, which unlocks speech
    if (next === "off") {
      announcer.mute();
      setSpoken(null);
      void api.voiceStop().catch(() => {}); // the worker process goes when voice does
    } else if (level === "off") {
      void speaker.say("Voice announcements on");
    }
  }

  const [voiceName, setVoiceNameState] = useState<string | null>(loadVoiceName);
  const voices = local.status?.state === "ready" ? naturalChoices(local.status.voices) : [];
  const selectedVoice = chosenVoice(voices, voiceName);
  chosenRef.current = selectedVoice;

  // Starting the natural voice takes a few seconds, so load it before the
  // first announcement rather than during it.
  useEffect(() => {
    if (active !== "off" && selectedVoice) void api.voiceWarm().catch(() => {});
  }, [active, selectedVoice]);

  return {
    level,
    active,
    ready,
    setLevel,
    voices,
    selectedVoice,
    local,
    fallbackReason: active !== "off" ? fallbackReason : null,
    setVoice: (name: string) => {
      saveVoiceName(name);
      setVoiceNameState(name);
    },
    preview: (name: string) => {
      speaker.stop();
      void speaker.say(PREVIEW_LINE, name);
    },
    spoken,
    paused: paused && active !== "off",
    dismiss: () => setSpoken(null),
    mute: () => { announcer.mute(); setSpoken(null); },
  };
}

// Listen before choosing: each voice has its own Preview.
export function VoicePicker({ voices, selected, onSelect, onPreview }: {
  voices: VoiceChoice[];
  selected: string | null;
  onSelect: (name: string) => void;
  onPreview: (name: string) => void;
}) {
  if (voices.length === 0) return null; // the download panel says what to do
  return (
    <div className="rd-voice-picker">
      <div className="rd-voice-picker-label" id="voice-picker-label">Voice</div>
      <div className="rd-voice-picker-list" role="radiogroup" aria-labelledby="voice-picker-label">
        {voices.map((v) => (
          <div key={v.name} className="rd-voice-picker-row">
            <label>
              <input type="radio" name="rd-voice-name" checked={v.name === selected} onChange={() => onSelect(v.name)} />
              {v.label}
            </label>
            <button type="button" className="rd-btn rd-btn-ghost rd-btn-sm" aria-label={`Preview ${v.label}`} onClick={() => onPreview(v.name)}>
              Preview
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// Natural voices are opt-in: say what they cost before anything downloads.
export function NaturalVoicePanel({ status, fallbackReason, onInstall, onRemove }: {
  status: LocalVoiceStatus | null;
  fallbackReason: string | null;
  onInstall: () => void;
  onRemove: () => void;
}) {
  if (!status) return null;
  return (
    <div className="rd-voice-natural" aria-live="polite">
      {status.state !== "ready" && <div className="rd-voice-picker-label">Voice</div>}
      {status.state === "unsupported" && <p className="rd-voice-picker-hint">{status.reason}</p>}
      {status.state === "absent" && (
        <>
          <p className="rd-voice-picker-hint">
            Voice announcements use Kokoro, an open-source voice that runs on this Mac. Nothing leaves the machine, and there is no per-announcement cost.
          </p>
          <button type="button" className="rd-btn rd-btn-sm" onClick={onInstall}>Download natural voices ({status.size})</button>
        </>
      )}
      {status.state === "installing" && (
        <p className="rd-voice-picker-hint" role="status">
          {status.step}… {Math.round(status.done * 100)}%
        </p>
      )}
      {status.state === "failed" && (
        <>
          <p className="rd-voice-picker-hint">The download didn't finish: {status.reason}</p>
          <button type="button" className="rd-btn rd-btn-sm" onClick={onInstall}>Try again</button>
        </>
      )}
      {status.state === "ready" && (
        <p className="rd-voice-picker-hint">
          Natural voices installed ·{" "}
          <button type="button" className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onRemove}>Remove</button>
        </p>
      )}
      {fallbackReason && (
        <p className="rd-voice-picker-hint" role="alert">
          The voice couldn't speak ({fallbackReason}), so Oracle chimed instead.
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

export function VoiceMenu({ level, ready, onLevel }: { level: VoiceLevel; ready: boolean; onLevel: (level: VoiceLevel) => void }) {
  if (!ready) {
    return <span className="rd-voice-menu" role="img" aria-label="Voice off — download a voice in Settings" title="Voice off — download a voice in Settings">🔇</span>;
  }
  return (
    <label className="rd-voice-menu" title="Oracle voice announcements. They only play while a dashboard is open.">
      <span aria-hidden="true">{level === "off" ? "🔇" : "🔊"}</span>
      <select aria-label="Voice announcements" value={level} onChange={(e) => onLevel(e.target.value as VoiceLevel)}>
        {VOICE_LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
      </select>
    </label>
  );
}

// The natural voice failed: Oracle chimes instead of speaking, and says why.
export function VoiceFallbackPill({ reason }: { reason: string }) {
  return (
    <span className="rd-voice-fallback" role="status" title={reason}>
      Voice unavailable: {reason}
    </span>
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
