import { afterEach, expect, it, vi } from "vitest";
import { RelayNote } from "./api";
import {
  Announcement,
  Announcer,
  COMPLETION_HOLD_MS,
  loadVoiceLevel,
  phrases,
  REANNOUNCE_MS,
  saveVoiceLevel,
  Speaker,
  VoiceSession,
  VoiceTracker,
} from "./voice";

afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

const T0 = 1_000_000;
const note = (p: Partial<RelayNote> & { id: string; session_key: string }): RelayNote => ({
  name: p.session_key, folder: "Duckterm", runtime: "claude-code", kind: "question", status: "open",
  created_at: T0, urgency: "blocked", ...p,
});
const sessions: VoiceSession[] = [
  { key: "a", label: "architect", group: "Duckterm", state: "busy" },
  { key: "m1", label: "main-dev", group: "Duckterm", state: "busy" },
  { key: "m2", label: "main-dev", group: "Nourish/app", state: "busy" },
];
const withState = (key: string, state: string) => sessions.map((s) => (s.key === key ? { ...s, state } : s));

function seeded(notes: RelayNote[] = []) {
  const t = new VoiceTracker();
  expect(t.update(sessions, notes, "done", T0)).toEqual([]); // the backlog on page load stays quiet
  return t;
}

it("announces a new needs-you note once, with a chime flag for approvals", () => {
  const t = seeded([note({ id: "old", session_key: "a" })]);
  const approval = note({ id: "n1", session_key: "a", kind: "approval" });
  expect(t.update(sessions, [approval], "done", T0 + 1)).toEqual([
    { kind: "needs", key: "a", name: "architect", approval: true },
  ]);
  expect(t.update(sessions, [approval], "done", T0 + 2)).toEqual([]);
});

it("re-announces a still-open note once after 15 minutes, then stays silent", () => {
  const t = seeded();
  const n = note({ id: "n1", session_key: "a" });
  t.update(sessions, [n], "needs", T0);
  expect(t.update(sessions, [n], "needs", T0 + REANNOUNCE_MS - 1)).toEqual([]);
  expect(t.update(sessions, [n], "needs", T0 + REANNOUNCE_MS)).toHaveLength(1);
  expect(t.update(sessions, [n], "needs", T0 + 3 * REANNOUNCE_MS)).toEqual([]);
});

it("speaks offers only at Everything, and never replays offers seen at a lower level", () => {
  const t = seeded();
  const offer = note({ id: "o1", session_key: "a", urgency: "offer" });
  expect(t.update(sessions, [offer], "done", T0 + 1)).toEqual([]);
  expect(t.update(sessions, [offer], "all", T0 + 2)).toEqual([]);
  const fresh = note({ id: "o2", session_key: "a", urgency: "offer" });
  expect(t.update(sessions, [offer, fresh], "all", T0 + 3)).toEqual([
    { kind: "offer", key: "a", name: "architect", approval: false },
  ]);
});

it("says a session is complete only after the hold, and not if it turned out to ask something", () => {
  const t = seeded();
  t.update(withState("a", "idle"), [], "done", T0 + 1);
  expect(t.update(withState("a", "idle"), [], "done", T0 + COMPLETION_HOLD_MS)).toEqual([]);
  expect(t.update(withState("a", "idle"), [], "done", T0 + 1 + COMPLETION_HOLD_MS)).toEqual([
    { kind: "done", key: "a", name: "architect", approval: false },
  ]);

  const asks = seeded();
  asks.update(withState("a", "idle"), [], "done", T0 + 1);
  const q = note({ id: "q", session_key: "a" });
  expect(asks.update(withState("a", "idle"), [q], "done", T0 + 30_000)).toHaveLength(1); // needs you
  expect(asks.update(withState("a", "idle"), [q], "done", T0 + 2 * COMPLETION_HOLD_MS)).toEqual([]);

  const resumed = seeded();
  resumed.update(withState("a", "idle"), [], "done", T0 + 1);
  resumed.update(sessions, [], "done", T0 + 10_000); // busy again before the hold ended
  expect(resumed.update(sessions, [], "done", T0 + 2 * COMPLETION_HOLD_MS)).toEqual([]);
});

it("keeps completions out of the Needs-you level and says nothing when off", () => {
  const t = seeded();
  t.update(withState("a", "idle"), [], "needs", T0 + 1);
  expect(t.update(withState("a", "idle"), [], "needs", T0 + 1 + COMPLETION_HOLD_MS)).toEqual([]);
  expect(t.update(sessions, [note({ id: "n", session_key: "a" })], "off", T0 + 2)).toEqual([]);
});

it("adds the folder only when two sessions share a name", () => {
  const t = seeded();
  const out = t.update(sessions, [note({ id: "x", session_key: "m2" }), note({ id: "y", session_key: "a" })], "done", T0 + 1);
  expect(out.map((a) => a.name)).toEqual(["main-dev in Nourish", "architect"]);
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
