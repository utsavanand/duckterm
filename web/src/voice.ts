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
    const fresh = !(p?.state === "waiting" && p.waitingSince === s.waitingSince);
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
export type Speaker = { say: (text: string) => Promise<boolean | void>; chime: () => Promise<void>; stop: () => void };

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
      new Promise<boolean>((resolve) => {
        if (!("speechSynthesis" in window)) return resolve(true);
        const u = new SpeechSynthesisUtterance(text);
        u.onend = () => resolve(true);
        u.onerror = (e) => resolve(e.error !== "not-allowed");
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
