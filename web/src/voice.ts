// Oracle voice mode: short spoken announcements ("architect needs your
// input", "main-dev is complete") so the owner can leave the screen.
// Spec: docs/oracle-voice-spec.md. Owner decisions, 2026-09-29: default level
// "needs you + done", a chime before approvals, one re-announcement after 15
// minutes, and speech through window.speechSynthesis, so it only speaks while
// a dashboard is open.

import { RelayNote } from "./api";

export type VoiceLevel = "off" | "needs" | "done" | "all";

export const VOICE_LEVELS: { value: VoiceLevel; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "needs", label: "Needs you" },
  { value: "done", label: "Needs you + done" },
  { value: "all", label: "Everything" },
];

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

export type Announcement = { kind: "needs" | "done" | "offer"; key: string; name: string; approval: boolean };

export type VoiceSession = { key: string; label: string; group?: string; state: string };

// A turn that ends on a question becomes a needs-you note about 30 s after
// Stop (the relay's settle wait plus one classifier call), so a completion
// waits this long and is dropped if a note or new work shows up meanwhile.
export const COMPLETION_HOLD_MS = 45_000;
export const REANNOUNCE_MS = 15 * 60_000;

// Turns snapshots of sessions and relay notes into announcements. Pure apart
// from its own memory, so tests drive it with plain objects and a clock.
export class VoiceTracker {
  private seeded = false;
  private announced = new Map<string, number>(); // note id -> first announced at
  private reannounced = new Set<string>();
  private prevState = new Map<string, string>();
  private pendingDone = new Map<string, number>(); // session key -> idle since

  update(sessions: VoiceSession[], notes: RelayNote[], level: VoiceLevel, now: number): Announcement[] {
    const open = notes.filter((n) => n.status === "open");
    const out: Announcement[] = [];
    const byKey = new Map(sessions.map((s) => [s.key, s]));
    const name = (key: string, fallback: string) => spokenName(key, sessions, fallback);

    if (!this.seeded) {
      // Don't read out the backlog on page load: only what happens from now on.
      for (const n of open) this.announced.set(n.id, now);
      for (const s of sessions) this.prevState.set(s.key, s.state);
      this.seeded = true;
      return [];
    }

    for (const n of open) {
      const offer = n.urgency === "offer";
      const first = this.announced.get(n.id);
      if (offer && level !== "all") {
        // Seen but not spoken, so switching to Everything later doesn't read out old offers.
        if (first === undefined) this.announced.set(n.id, now);
        continue;
      }
      const approval = n.kind === "approval";
      if (first === undefined) {
        this.announced.set(n.id, now);
        out.push({ kind: offer ? "offer" : "needs", key: n.session_key, name: name(n.session_key, n.name), approval });
      } else if (!offer && now - first >= REANNOUNCE_MS && !this.reannounced.has(n.id)) {
        this.reannounced.add(n.id);
        out.push({ kind: "needs", key: n.session_key, name: name(n.session_key, n.name), approval });
      }
    }

    const asking = new Set(open.map((n) => n.session_key));
    for (const s of sessions) {
      const prev = this.prevState.get(s.key);
      if (prev === "busy" && s.state === "idle") this.pendingDone.set(s.key, now);
      if (s.state !== "idle") this.pendingDone.delete(s.key);
      this.prevState.set(s.key, s.state);
    }
    for (const [key, since] of this.pendingDone) {
      if (asking.has(key) || !byKey.has(key)) {
        this.pendingDone.delete(key);
      } else if (now - since >= COMPLETION_HOLD_MS) {
        this.pendingDone.delete(key);
        if (level === "done" || level === "all") {
          out.push({ kind: "done", key, name: name(key, key), approval: false });
        }
      }
    }
    if (level === "off") return [];
    return out;
  }
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

export type Speaker = { say: (text: string) => Promise<void>; chime: () => Promise<void>; stop: () => void };

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

  private async run(): Promise<void> {
    this.running = true;
    const gen = this.generation;
    try {
      while (this.queue.length && gen === this.generation) {
        await this.sleep(this.opts.gatherMs); // let a pile-up arrive, then speak it as one
        while (this.now() - this.typingAt() < this.opts.typingQuietMs && gen === this.generation) {
          await this.sleep(this.opts.pollMs);
        }
        const batch = this.queue.splice(0);
        for (const line of phrases(batch)) {
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

// The browser's own speech and a short two-note chime. Nothing leaves the
// machine; voice and rate are the OS defaults.
export function browserSpeaker(): Speaker {
  let audio: AudioContext | null = null;
  return {
    say: (text) =>
      new Promise<void>((resolve) => {
        if (!("speechSynthesis" in window)) return resolve();
        const u = new SpeechSynthesisUtterance(text);
        u.onend = () => resolve();
        u.onerror = () => resolve();
        window.speechSynthesis.speak(u);
      }),
    chime: async () => {
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
    },
    stop: () => {
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    },
  };
}
