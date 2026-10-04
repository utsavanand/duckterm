import { afterEach, expect, it, vi } from "vitest";
import { RelayNote } from "./api";
import {
  announce,
  chosenVoice,
  naturalChoices,
  loadVoiceName,
  saveVoiceName,
  VoiceChoice,
  Announcement,
  Announcer,
  loadVoiceLevel,
  phrases,
  REANNOUNCE_MS,
  saveVoiceLevel,
  Speaker,
  VoiceLevel,
  VoiceSession,
  VoiceSnapshot,
} from "./voice";

afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

const T0 = 1_000_000;
const note = (p: Partial<RelayNote> & { id: string; session_key: string }): RelayNote => ({
  name: p.session_key, folder: "Duckterm", runtime: "claude-code", kind: "question", status: "open",
  created_at: T0, urgency: "blocked", ...p,
});
const base: VoiceSession[] = [
  { key: "a", label: "architect", group: "Duckterm", state: "busy" },
  { key: "m1", label: "main-dev", group: "Duckterm", state: "busy" },
  { key: "m2", label: "main-dev", group: "Nourish/app", state: "busy" },
];
const set = (key: string, p: Partial<VoiceSession>) => base.map((s) => (s.key === key ? { ...s, ...p } : s));
const snap = (at: number, sessions = base, notes: RelayNote[] | null = []): VoiceSnapshot => ({ at, sessions, notes });
const say = (prev: VoiceSnapshot | null, next: VoiceSnapshot, level: VoiceLevel = "done") => announce(prev, next, level);

it("stays quiet on page load, then announces a session that starts waiting, chiming for approvals", () => {
  const waiting = set("a", { state: "waiting", waitingSince: T0, waitingCause: "approval" });
  expect(say(null, snap(T0, waiting))).toEqual([]);
  expect(say(snap(T0), snap(T0 + 1, waiting))).toEqual([{ kind: "needs", key: "a", name: "architect", approval: true }]);
  expect(say(snap(T0 + 1, waiting), snap(T0 + 2, waiting))).toEqual([]); // same wait
});

it("stays quiet about sessions that were already waiting when they first appear", () => {
  // main-qa's PR #156 reproducer: the first session load arrives after the first snapshot.
  const before = snap(T0 + 5000, []);
  const old = set("a", { state: "waiting", waitingSince: T0, waitingCause: "approval" });
  expect(say(before, snap(T0 + 6000, old))).toEqual([]);
  const newcomer = set("a", { state: "waiting", waitingSince: T0 + 5500 });
  expect(say(before, snap(T0 + 6000, newcomer))).toHaveLength(1); // a new session that starts waiting
});

it("re-announces a wait once as it crosses 15 minutes, and a new wait announces afresh", () => {
  const w = set("a", { state: "waiting", waitingSince: T0, waitingCause: "question" });
  expect(say(snap(T0 + REANNOUNCE_MS - 5000, w), snap(T0 + REANNOUNCE_MS, w))).toHaveLength(1);
  expect(say(snap(T0 + REANNOUNCE_MS, w), snap(T0 + 3 * REANNOUNCE_MS, w))).toEqual([]);
  const again = set("a", { state: "waiting", waitingSince: T0 + 60_000 });
  expect(say(snap(T0 + 30_000, w), snap(T0 + 60_000, again))).toHaveLength(1); // unblocked and re-blocked
});

it("announces a turn that ended on a question, which never shows as waiting", () => {
  const q = note({ id: "q1", session_key: "a" });
  expect(say(snap(T0, base, []), snap(T0 + 1, base, [q]))).toEqual([{ kind: "needs", key: "a", name: "architect", approval: false }]);
  expect(say(snap(T0, base, null), snap(T0 + 1, base, [q]))).toEqual([]); // the relay's first load is not news
  const waitingToo = set("a", { state: "waiting", waitingSince: T0 });
  expect(say(snap(T0, waitingToo, []), snap(T0 + 1, waitingToo, [q]))).toEqual([]); // the wait was already said
});

it("says complete on effective busy-to-idle, not while a question is open, and not below done", () => {
  const idle = set("a", { state: "idle" });
  expect(say(snap(T0), snap(T0 + 1, idle))).toEqual([{ kind: "done", key: "a", name: "architect", approval: false }]);
  const q = note({ id: "q1", session_key: "a" });
  expect(say(snap(T0, base, [q]), snap(T0 + 1, idle, [q]))).toEqual([]);
  expect(say(snap(T0), snap(T0 + 1, idle), "needs")).toEqual([]);
});

it("speaks offers only at Everything, and nothing when off", () => {
  const offer = note({ id: "o1", session_key: "a", urgency: "offer" });
  expect(say(snap(T0), snap(T0 + 1, base, [offer]))).toEqual([]);
  expect(say(snap(T0), snap(T0 + 1, base, [offer]), "all")).toEqual([{ kind: "offer", key: "a", name: "architect", approval: false }]);
  const waiting = set("a", { state: "waiting", waitingSince: T0 });
  expect(say(snap(T0), snap(T0 + 1, waiting), "off")).toEqual([]);
});

it("adds the folder only when two sessions share a name", () => {
  const out = say(snap(T0), snap(T0 + 1, set("m2", { state: "waiting", waitingSince: T0 })));
  expect(out.map((a) => a.name)).toEqual(["main-dev in Nourish"]);
  expect(say(snap(T0), snap(T0 + 1, set("a", { state: "waiting", waitingSince: T0 }))).map((a) => a.name)).toEqual(["architect"]);
});

const ann = (kind: Announcement["kind"], name: string, approval = false): Announcement => ({ kind, key: name, name, approval });

it("reads one or two names, and counts three or more", () => {
  expect(phrases([ann("needs", "architect", true), ann("done", "main-dev")])).toEqual([
    { text: "architect needs your input", chime: true },
    { text: "main-dev is complete", chime: false },
  ]);
  expect(phrases([ann("needs", "a"), ann("needs", "b", true), ann("needs", "c")])).toEqual([
    { text: "three sessions need you", chime: true },
  ]);
});

function fakeSpeaker() {
  const log: string[] = [];
  let speaking = 0;
  const speaker: Speaker & { log: string[]; overlapped: boolean } = {
    log,
    overlapped: false,
    say: async (text) => {
      if (speaking) speaker.overlapped = true;
      speaking += 1;
      log.push(text);
      await Promise.resolve();
      speaking -= 1;
    },
    chime: async () => { log.push("<chime>"); },
    stop: () => { log.push("<stop>"); },
  };
  return speaker;
}

function announcer(speaker: Speaker, typingAt = () => 0) {
  let clock = T0;
  const sleep = async (ms: number) => { clock += ms; };
  return new Announcer(speaker, typingAt, { gatherMs: 1500, typingQuietMs: 5000, pollMs: 500 }, sleep, () => clock);
}

const settle = () => new Promise((r) => setTimeout(r, 0));

it("speaks one line at a time with the chime first, never overlapping", async () => {
  const speaker = fakeSpeaker();
  const a = announcer(speaker);
  a.add([ann("needs", "architect", true), ann("done", "main-dev")]);
  a.add([ann("done", "ui-dev")]);
  await settle();
  expect(speaker.log).toEqual(["<chime>", "architect needs your input", "main-dev is complete", "ui-dev is complete"]);
  expect(speaker.overlapped).toBe(false);
});

it("holds while the owner is typing, then speaks", async () => {
  const speaker = fakeSpeaker();
  let clock = T0;
  const a = new Announcer(speaker, () => T0 + 3000, { gatherMs: 1500, typingQuietMs: 5000, pollMs: 500 }, async (ms) => { clock += ms; }, () => clock);
  a.add([ann("needs", "architect")]);
  await settle();
  expect(clock - T0).toBeGreaterThanOrEqual(8000); // waited until 5 s after the last keystroke
  expect(speaker.log).toEqual(["architect needs your input"]);
});

it("mute stops the current line and drops everything queued", async () => {
  const speaker = fakeSpeaker();
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  const a = announcer(speaker);
  a.add([ann("needs", "architect")]);
  a.mute();
  release();
  await gate;
  await settle();
  expect(speaker.log).toEqual(["<stop>"]);
});

it("stores the level and reads it back, defaulting to needs-you plus done", () => {
  expect(loadVoiceLevel()).toBe("done");
  saveVoiceLevel("needs");
  expect(loadVoiceLevel()).toBe("needs");
  localStorage.setItem("rd.voice", "loud");
  expect(loadVoiceLevel()).toBe("done");
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  expect(loadVoiceLevel()).toBe("done");
});

// From main-qa's PR #156 report: typing that starts during one line must hold the next.
it("holds the next line when typing starts during the current one", async () => {
  let clock = T0;
  let typed = 0;
  const saidAt: number[] = [];
  const speaker: Speaker = {
    say: async () => { saidAt.push(clock); if (saidAt.length === 1) typed = clock; },
    chime: async () => {},
    stop: () => {},
  };
  const a = new Announcer(speaker, () => typed, { gatherMs: 1500, typingQuietMs: 5000, pollMs: 500 }, async (ms) => { clock += ms; }, () => clock);
  a.add([ann("needs", "architect"), ann("needs", "main-dev")]);
  await settle();
  expect(saidAt).toHaveLength(2);
  expect(saidAt[1] - saidAt[0]).toBeGreaterThanOrEqual(5000);
});

const NATURAL: VoiceChoice[] = [
  { name: "kokoro:af_bella", label: "Bella (US)" },
  { name: "kokoro:af_heart", label: "Heart (US)" },
  { name: "kokoro:bf_emma", label: "Emma (UK)" },
];

it("chooses the stored natural voice, else Heart (US), the owner's pick", () => {
  expect(chosenVoice(NATURAL, "kokoro:bf_emma")).toBe("kokoro:bf_emma");
  expect(chosenVoice(NATURAL, null)).toBe("kokoro:af_heart");
  expect(chosenVoice(NATURAL, "Samantha")).toBe("kokoro:af_heart"); // an old macOS choice is ignored
  expect(chosenVoice([], "kokoro:af_heart")).toBeNull(); // nothing installed: nothing to speak with
});

it("labels natural voices by name and accent", () => {
  expect(naturalChoices([{ id: "af_heart", label: "Heart", accent: "US" }])).toEqual([
    { name: "kokoro:af_heart", label: "Heart (US)" },
  ]);
});

it("stores the chosen voice and reads it back", () => {
  expect(loadVoiceName()).toBeNull();
  saveVoiceName("kokoro:bf_emma");
  expect(loadVoiceName()).toBe("kokoro:bf_emma");
});
