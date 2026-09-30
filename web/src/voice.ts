// Oracle voice mode: short spoken announcements ("architect needs your
// input", "main-dev is complete") so the owner can leave the screen.
// Requirements: docs/oracle-voice-spec.md (owner decisions, 2026-09-29).
// Design: architect's "Design — Oracle voice mode". Speech goes through
// window.speechSynthesis, so it only speaks while a dashboard is open.

import { RelayNote } from "./api";

export type VoiceLevel = "off" | "needs" | "done" | "all";

export const VOICE_LEVELS: { value: VoiceLevel; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "needs", label: "Needs you" },
  { value: "done", label: "Needs you + done" },
  { value: "all", label: "Everything" },
];

// Per device on purpose: the office Mac and a phone browser shouldn't share
// audio settings. (Desktop notifications, B6, are a separate setting.)
const STORAGE_KEY = "rd.voice";
const DEFAULT_LEVEL: VoiceLevel = "done";

export function loadVoiceLevel(): VoiceLevel {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (VOICE_LEVELS.some((l) => l.value === stored)) return stored as VoiceLevel;
  } catch { /* storage blocked: fall back to the default */ }
  return DEFAULT_LEVEL;
}

export function saveVoiceLevel(level: VoiceLevel): void {
  try {
    localStorage.setItem(STORAGE_KEY, level);
  } catch { /* storage blocked: the choice lasts until reload */ }
}

// ── Choosing a voice ──
// Natural (Kokoro) voices only. The owner turned the macOS voices down four
// times ("Remove existing Mac voices from the options. They suck.", 2026-09-30),
// so they are neither offered nor used as a fallback.

// name is what gets stored ("kokoro:<id>"); label is what the picker shows.
export type VoiceChoice = { name: string; label: string };

export const NATURAL_PREFIX = "kokoro:";
export const DEFAULT_NATURAL = `${NATURAL_PREFIX}af_heart`;

export function naturalChoices(voices: { id: string; label: string; accent: string }[]): VoiceChoice[] {
  return voices.map((v) => ({ name: NATURAL_PREFIX + v.id, label: `${v.label} (${v.accent})` }));
}

// The stored choice if it's installed, else Heart (US), the owner's pick.
export function chosenVoice(voices: VoiceChoice[], stored: string | null): string | null {
  return (
    voices.find((v) => v.name === stored)?.name ??
    voices.find((v) => v.name === DEFAULT_NATURAL)?.name ??
    voices[0]?.name ??
    null
  );
}

const VOICE_NAME_KEY = "rd.voice.name";

export function loadVoiceName(): string | null {
  try {
    return localStorage.getItem(VOICE_NAME_KEY);
  } catch {
    return null;
  }
}

export function saveVoiceName(name: string): void {
  try {
    localStorage.setItem(VOICE_NAME_KEY, name);
  } catch { /* storage blocked: the choice lasts until reload */ }
}

export type Announcement = { kind: "needs" | "done" | "offer"; key: string; name: string; approval: boolean };

export type VoiceSession = {
  key: string;
  label: string;
  group?: string;
  state: string; // effective state: idle only after the post-Stop settle grace
  waitingSince?: number;
  waitingCause?: string;
};

// notes is null until the relay has loaded, so a late first load isn't
// mistaken for a burst of new questions.
export type VoiceSnapshot = { at: number; sessions: VoiceSession[]; notes: RelayNote[] | null };

export const REANNOUNCE_MS = 15 * 60_000;

// Every rule lives here, as a pure diff of two snapshots (architect's design,
// 2026-09-29): what changed between them is what gets said.
//   needs you: a session that starts waiting (approvals get the chime), or a
//     relay "question" note for a turn that ended asking the owner, which
//     never shows as waiting in the fold.
//   re-announce: a wait or question still open 15 minutes after it began,
//     said once as the boundary is crossed.
//   done: effective busy -> idle, skipped while a question is open for it.
//   offers: new offer notes, at Everything only.
export function announce(prev: VoiceSnapshot | null, next: VoiceSnapshot, level: VoiceLevel): Announcement[] {
  if (!prev || level === "off") return []; // page load: the backlog stays quiet
  const out: Announcement[] = [];
  const name = (key: string, fallback: string) => spokenName(key, next.sessions, fallback);
  const before = new Map(prev.sessions.map((s) => [s.key, s]));
  const crossed = (since: number) => since + REANNOUNCE_MS > prev.at && since + REANNOUNCE_MS <= next.at;

  for (const s of next.sessions) {
    if (s.state !== "waiting") continue;
    const p = before.get(s.key);
    const since = s.waitingSince ?? next.at;
    // A session seen for the first time (the page's first session load, a
    // remote host, replayed events) is only news if its wait began after the
    // last look; otherwise it was already waiting before voice could see it.
    const fresh = p ? !(p.state === "waiting" && p.waitingSince === s.waitingSince) : since > prev.at;
    if (fresh || crossed(since)) {
      out.push({ kind: "needs", key: s.key, name: name(s.key, s.label), approval: s.waitingCause === "approval" });
    }
  }

  const waiting = new Set(next.sessions.filter((s) => s.state === "waiting").map((s) => s.key));
  const openQuestions = (next.notes ?? []).filter((n) => n.status === "open" && n.kind === "question");
  if (next.notes && prev.notes) {
    const seen = new Set(prev.notes.filter((n) => n.status === "open").map((n) => n.id));
    for (const n of openQuestions) {
      if (waiting.has(n.session_key)) continue; // the wait itself is announced
      const offer = n.urgency === "offer";
      if (offer && level !== "all") continue;
      if (!seen.has(n.id) || (!offer && crossed(n.created_at))) {
        out.push({ kind: offer ? "offer" : "needs", key: n.session_key, name: name(n.session_key, n.name), approval: false });
      }
    }
  }

  if (level === "done" || level === "all") {
    const asking = new Set(openQuestions.filter((n) => n.urgency !== "offer").map((n) => n.session_key));
    for (const s of next.sessions) {
      const p = before.get(s.key);
      if (s.state === "idle" && p?.state === "busy" && !asking.has(s.key)) {
        out.push({ kind: "done", key: s.key, name: name(s.key, s.label), approval: false });
      }
    }
  }
  return out;
}

// Several sessions share names like "main-dev"; add the folder only then.
export function spokenName(key: string, sessions: VoiceSession[], fallback: string): string {
  const me = sessions.find((s) => s.key === key);
  if (!me) return fallback;
  const twins = sessions.filter((s) => s.label === me.label);
  const folder = me.group?.split("/")[0];
  return twins.length > 1 && folder ? `${me.label} in ${folder}` : me.label;
}

const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
const count = (n: number) => NUMBER_WORDS[n] ?? String(n);

// What to say for one batch: each name when there are one or two of a kind,
// a count from three up.
export function phrases(batch: Announcement[]): { text: string; chime: boolean }[] {
  const out: { text: string; chime: boolean }[] = [];
  const kinds: [Announcement["kind"], string, (n: string) => string][] = [
    ["needs", "sessions need you", (n) => `${n} needs your input`],
    ["done", "sessions are complete", (n) => `${n} is complete`],
    ["offer", "sessions have a suggestion", (n) => `${n} has a suggestion`],
  ];
  for (const [kind, many, one] of kinds) {
    const items = batch.filter((a) => a.kind === kind);
    if (items.length >= 3) {
      out.push({ text: `${count(items.length)} ${many}`, chime: items.some((a) => a.approval) });
    } else {
      for (const a of items) out.push({ text: one(a.name), chime: a.approval });
    }
  }
  return out;
}

// say resolves false when the browser refuses to speak (no user gesture yet).
// voice names a specific voice, for previews; otherwise the chosen one is used.
export type Speaker = {
  say: (text: string, voice?: string) => Promise<boolean | void>;
  chime: () => Promise<void>;
  stop: () => void;
};

// Speaks batches one line at a time, never overlapping. Holds while the owner
// is typing, and mute() drops everything queued and stops the current line.
export class Announcer {
  private queue: Announcement[] = [];
  private running = false;
  private generation = 0;

  constructor(
    private speaker: Speaker,
    private typingAt: () => number,
    private opts = { gatherMs: 1500, typingQuietMs: 5000, pollMs: 500 },
    private sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms)),
    private now = () => Date.now(),
  ) {}

  add(items: Announcement[]): void {
    if (!items.length) return;
    this.queue.push(...items);
    if (!this.running) void this.run();
  }

  mute(): void {
    this.generation += 1;
    this.queue = [];
    this.speaker.stop();
  }

  private async quiet(gen: number): Promise<void> {
    while (this.now() - this.typingAt() < this.opts.typingQuietMs && gen === this.generation) {
      await this.sleep(this.opts.pollMs);
    }
  }

  private async run(): Promise<void> {
    this.running = true;
    const gen = this.generation;
    try {
      while (this.queue.length && gen === this.generation) {
        await this.sleep(this.opts.gatherMs); // let a pile-up arrive, then speak it as one
        const batch = this.queue.splice(0);
        for (const line of phrases(batch)) {
          // Typing can start mid-batch, so wait before every line, not once per batch.
          await this.quiet(gen);
          if (gen !== this.generation) return;
          if (line.chime) await this.speaker.chime();
          if (gen !== this.generation) return;
          await this.speaker.say(line.text);
        }
      }
    } finally {
      this.running = false;
      if (this.queue.length && gen !== this.generation) void this.run();
    }
  }
}

// A short two-note chime from Web Audio: before approvals, and on its own
// when the natural voice can't speak.
export function chimePlayer(): () => Promise<void> {
  let audio: AudioContext | null = null;
  return async () => {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    audio ??= new Ctx();
    if (audio.state === "suspended") await audio.resume().catch(() => {});
    const start = audio.currentTime;
    for (const [i, freq] of [660, 880].entries()) {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const t = start + i * 0.14;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.12, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.13);
      osc.connect(gain).connect(audio.destination);
      osc.start(t);
      osc.stop(t + 0.14);
    }
    await new Promise((r) => setTimeout(r, 350));
  };
}

// Speaks through Oracle's local Kokoro voice. When it can't (worker failed,
// timed out, not installed), it chimes instead and reports why; the owner
// asked for no macOS voice, even as a fallback (2026-09-30). onFallback's
// reason shows in the header; null clears it.
export function naturalSpeaker(opts: {
  fetchWav: (text: string, voice: string) => Promise<Blob>;
  play: (wav: Blob) => Promise<boolean>;
  stopPlayback: () => void;
  chime: () => Promise<void>;
  chosen: () => string | null;
  onFallback: (reason: string | null) => void;
}): Speaker {
  return {
    say: async (text, voiceName) => {
      const name = voiceName ?? opts.chosen();
      try {
        if (!name?.startsWith(NATURAL_PREFIX)) throw new Error("no natural voice is installed");
        const spoke = await opts.play(await opts.fetchWav(text, name.slice(NATURAL_PREFIX.length)));
        opts.onFallback(null);
        return spoke;
      } catch (e) {
        const reason = (e as Error).message || "the natural voice failed";
        console.warn(`[duckterm] voice: couldn't say "${text}": ${reason}`);
        opts.onFallback(reason);
        await opts.chime();
        return true;
      }
    },
    chime: opts.chime,
    stop: opts.stopPlayback,
  };
}

// Plays a WAV and resolves when it ends. false means the browser refused
// (no user gesture yet), which the dashboard shows as "Voice paused".
export function audioPlayer(): { play: (wav: Blob) => Promise<boolean>; stop: () => void } {
  let current: HTMLAudioElement | null = null;
  return {
    play: (wav) =>
      new Promise<boolean>((resolve, reject) => {
        const url = URL.createObjectURL(wav);
        const audio = new Audio(url);
        current = audio;
        const done = (ok: boolean) => { URL.revokeObjectURL(url); if (current === audio) current = null; resolve(ok); };
        audio.onended = () => done(true);
        audio.onpause = () => done(true); // stopped by mute
        audio.onerror = () => { URL.revokeObjectURL(url); reject(new Error("the audio could not be played")); };
        audio.play().catch((e: Error) => (e.name === "NotAllowedError" ? done(false) : reject(e)));
      }),
    stop: () => { current?.pause(); current = null; },
  };
}
