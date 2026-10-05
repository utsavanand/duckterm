import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, LocalVoiceStatus, RelayNote } from "./api";
import { useVoice, VoiceMenu, VoicePausedPill, VoicePicker, VoiceToast } from "./VoiceControl";
import { Speaker, VoiceSession } from "./voice";

const relay = vi.hoisted(() => ({ notes: [] as RelayNote[] }));
vi.mock("./relay", () => ({ useRelay: () => ({ notes: relay.notes, rules: [], open: 0, refresh: () => {}, loaded: true }) }));
vi.mock("./api", () => ({
  api: { voiceStatus: vi.fn(), voiceInstall: vi.fn(), voiceRemove: vi.fn(), voiceSay: vi.fn(), voiceWarm: vi.fn(), voiceStop: vi.fn() },
}));

const READY: LocalVoiceStatus = {
  state: "ready",
  voices: [
    { id: "af_heart", label: "Heart", accent: "US" },
    { id: "bf_emma", label: "Emma", accent: "UK" },
  ],
};
beforeEach(() => {
  vi.mocked(api.voiceStatus).mockResolvedValue(READY);
  vi.mocked(api.voiceWarm).mockResolvedValue(undefined);
  vi.mocked(api.voiceStop).mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); localStorage.clear(); relay.notes = []; vi.resetAllMocks(); vi.useRealTimers(); });

// The announcer gathers a pile-up for 1.5 s before speaking. Tests that wait
// for speech drive the clock themselves instead of waiting on a real one,
// which timed out under load (release-dev, 2026-10-03: 3 failures in 8 runs).
const PAST_GATHER_MS = 2000;
const advance = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

const idle: VoiceSession[] = [{ key: "a", label: "architect", group: "Duckterm", state: "idle" }];
const approval: VoiceSession[] = [{ ...idle[0], state: "waiting", waitingSince: 5, waitingCause: "approval" }];

function Harness({ speaker, sessions = idle }: { speaker: Speaker; sessions?: VoiceSession[] }) {
  const voice = useVoice(sessions, speaker);
  return (
    <>
      <VoiceMenu level={voice.level} ready={voice.ready} onLevel={voice.setLevel} />
      {voice.spoken && <VoiceToast text={voice.spoken} level={voice.level} onLevel={voice.setLevel} onMute={voice.mute} onDismiss={voice.dismiss} />}
    </>
  );
}

const recorder = (said: string[]): Speaker => ({
  say: async (t) => { said.push(t); },
  chime: async () => { said.push("<chime>"); },
  stop: () => {},
});

it("chimes and speaks a session that starts waiting on an approval, and drops to needs-you in one click that persists", async () => {
  vi.useFakeTimers();
  const said: string[] = [];
  const view = render(<Harness speaker={recorder(said)} />);
  await advance();
  screen.getByRole("option", { name: "Needs you + done" }); // the natural voice is ready
  view.rerender(<Harness speaker={recorder(said)} sessions={approval} />);
  await advance(PAST_GATHER_MS);
  expect(said).toEqual(["<chime>", "architect needs your input"]);
  expect(screen.getByRole("status")).toHaveTextContent("architect needs your input");

  fireEvent.click(screen.getByRole("button", { name: "Only needs-you" }));
  expect(screen.getByLabelText("Voice announcements")).toHaveValue("needs");
  expect(localStorage.getItem("rd.voice")).toBe("needs");

  cleanup(); // a reload reads the stored choice back
  render(<Harness speaker={recorder(said)} />);
  await advance();
  expect(screen.getByLabelText("Voice announcements")).toHaveValue("needs");
});

it("stays off with the reason shown until a natural voice is downloaded", async () => {
  vi.useFakeTimers();
  vi.mocked(api.voiceStatus).mockResolvedValue({ state: "absent", size: "about 310 MB" });
  const said: string[] = [];
  const view = render(<Harness speaker={recorder(said)} />);
  await advance();
  const indicator = screen.getByRole("img", { name: "Voice off — download a voice in Settings" });
  expect(indicator).toHaveAttribute("title", "Voice off — download a voice in Settings");
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  view.rerender(<Harness speaker={recorder(said)} sessions={approval} />);
  await advance(PAST_GATHER_MS);
  expect(said).toEqual([]); // nothing to speak with, so nothing is said
  expect(api.voiceWarm).not.toHaveBeenCalled();
});

it("turning voice off stops speech at once and stops the voice process", async () => {
  const stop = vi.fn();
  render(<Harness speaker={{ say: async () => {}, chime: async () => {}, stop }} />);
  const menu = await screen.findByRole("option", { name: "Off" });
  await act(async () => {
    fireEvent.change(menu.closest("select")!, { target: { value: "off" } });
  });
  expect(stop).toHaveBeenCalled();
  expect(api.voiceStop).toHaveBeenCalled();
  expect(localStorage.getItem("rd.voice")).toBe("off");
});

it("speaks a turn that ended on a question, from the relay's note", async () => {
  vi.useFakeTimers();
  const said: string[] = [];
  const view = render(<Harness speaker={recorder(said)} />);
  await advance();
  relay.notes = [{ id: "q1", session_key: "a", name: "architect", folder: "Duckterm", runtime: "codex", kind: "question", status: "open", urgency: "blocked", created_at: 1 }];
  view.rerender(<Harness speaker={recorder(said)} sessions={[...idle]} />);
  await advance(PAST_GATHER_MS);
  expect(said).toEqual(["architect needs your input"]);
});

it("shows the paused pill after a reload until the page gets a click", async () => {
  localStorage.setItem("rd.voice", "done");
  Object.defineProperty(navigator, "userActivation", { value: { hasBeenActive: false }, configurable: true });
  function Paused() {
    const voice = useVoice(idle, recorder([]));
    return voice.paused ? <VoicePausedPill /> : null;
  }
  render(<Paused />);
  expect(await screen.findByRole("status")).toHaveTextContent("Voice paused. Click anywhere to resume.");
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("status")).toBeNull();
  Object.defineProperty(navigator, "userActivation", { value: undefined, configurable: true });
});

it("shows the paused pill when the browser refuses to play", async () => {
  const speaker: Speaker = { say: async () => false, chime: async () => {}, stop: () => {} };
  function Refused({ sessions }: { sessions: VoiceSession[] }) {
    const voice = useVoice(sessions, speaker);
    return <>{voice.ready && <span>ready</span>}{voice.paused && <VoicePausedPill />}</>;
  }
  vi.useFakeTimers();
  const view = render(<Refused sessions={idle} />);
  await advance();
  screen.getByText("ready");
  view.rerender(<Refused sessions={approval} />);
  await advance(PAST_GATHER_MS);
  expect(screen.getByRole("status")).toHaveTextContent("Voice paused");
});

it("lists only natural voices, previews each by name, and selects one", () => {
  const onPreview = vi.fn();
  const onSelect = vi.fn();
  const voices = [{ name: "kokoro:af_heart", label: "Heart (US)" }, { name: "kokoro:bf_emma", label: "Emma (UK)" }];
  render(<VoicePicker voices={voices} selected="kokoro:af_heart" onSelect={onSelect} onPreview={onPreview} />);
  expect(screen.getAllByRole("radio").map((r) => r.closest("label")!.textContent)).toEqual(["Heart (US)", "Emma (UK)"]);
  fireEvent.click(screen.getByRole("button", { name: "Preview Emma (UK)" }));
  expect(onPreview).toHaveBeenCalledWith("kokoro:bf_emma");
  fireEvent.click(screen.getByRole("radio", { name: "Emma (UK)" }));
  expect(onSelect).toHaveBeenCalledWith("kokoro:bf_emma");
  expect(screen.queryByText(/Manage Voices|Spoken Content/)).toBeNull();
  cleanup();
  const empty = render(<VoicePicker voices={[]} selected={null} onSelect={onSelect} onPreview={onPreview} />);
  expect(empty.container).toBeEmptyDOMElement(); // nothing installed: the download panel speaks for itself
});

it("defaults to Heart, persists a new choice, and previews with the sample line", async () => {
  const said: [string, string | undefined][] = [];
  const speaker: Speaker = { say: async (t, v) => { said.push([t, v]); }, chime: async () => {}, stop: () => {} };
  let voice!: ReturnType<typeof useVoice>;
  function Picker() { voice = useVoice(idle, speaker); return null; }
  render(<Picker />);
  await waitFor(() => expect(voice.selectedVoice).toBe("kokoro:af_heart"));
  act(() => voice.setVoice("kokoro:bf_emma"));
  expect(localStorage.getItem("rd.voice.name")).toBe("kokoro:bf_emma");
  expect(voice.selectedVoice).toBe("kokoro:bf_emma");
  act(() => voice.preview("kokoro:bf_emma"));
  expect(said).toEqual([["architect needs your input", "kokoro:bf_emma"]]);
});
