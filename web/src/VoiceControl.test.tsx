import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RelayNote } from "./api";
import { useVoice, VoiceMenu, VoicePausedPill, VoiceToast } from "./VoiceControl";
import { Speaker, VoiceSession } from "./voice";

const relay = vi.hoisted(() => ({ notes: [] as RelayNote[] }));
vi.mock("./relay", () => ({ useRelay: () => ({ notes: relay.notes, rules: [], open: 0, refresh: () => {}, loaded: true }) }));
afterEach(() => { cleanup(); localStorage.clear(); relay.notes = []; });

const idle: VoiceSession[] = [{ key: "a", label: "architect", group: "Duckterm", state: "idle" }];
const approval: VoiceSession[] = [{ ...idle[0], state: "waiting", waitingSince: 5, waitingCause: "approval" }];

function Harness({ speaker, sessions = idle }: { speaker: Speaker; sessions?: VoiceSession[] }) {
  const voice = useVoice(sessions, speaker);
  return (
    <>
      <VoiceMenu level={voice.level} onLevel={voice.setLevel} />
      {voice.spoken && <VoiceToast text={voice.spoken} level={voice.level} onLevel={voice.setLevel} onMute={voice.mute} onDismiss={voice.dismiss} />}
    </>
  );
}

it("chimes and speaks a session that starts waiting on an approval, and drops to needs-you in one click that persists", async () => {
  const said: string[] = [];
  const speaker: Speaker = { say: async (t) => { said.push(t); }, chime: async () => { said.push("<chime>"); }, stop: () => {} };
  const view = render(<Harness speaker={speaker} />);
  view.rerender(<Harness speaker={speaker} sessions={approval} />);
  await waitFor(() => expect(said).toEqual(["<chime>", "architect needs your input"]), { timeout: 4000 });
  expect(screen.getByRole("status")).toHaveTextContent("architect needs your input");

  fireEvent.click(screen.getByRole("button", { name: "Only needs-you" }));
  expect(screen.getByLabelText("Voice announcements")).toHaveValue("needs");
  expect(localStorage.getItem("rd.voice")).toBe("needs");

  cleanup(); // a reload reads the stored choice back
  render(<Harness speaker={speaker} />);
  expect(screen.getByLabelText("Voice announcements")).toHaveValue("needs");
});

it("turning voice off stops speech at once", async () => {
  const stop = vi.fn();
  const speaker: Speaker = { say: async () => {}, chime: async () => {}, stop };
  render(<Harness speaker={speaker} />);
  await act(async () => {
    fireEvent.change(screen.getByLabelText("Voice announcements"), { target: { value: "off" } });
  });
  expect(stop).toHaveBeenCalled();
  expect(localStorage.getItem("rd.voice")).toBe("off");
});

it("speaks a turn that ended on a question, from the relay's note", async () => {
  const said: string[] = [];
  const speaker: Speaker = { say: async (t) => { said.push(t); }, chime: async () => {}, stop: () => {} };
  const view = render(<Harness speaker={speaker} />);
  relay.notes = [{ id: "q1", session_key: "a", name: "architect", folder: "Duckterm", runtime: "codex", kind: "question", status: "open", urgency: "blocked", created_at: 1 }];
  view.rerender(<Harness speaker={speaker} sessions={[...idle]} />);
  await waitFor(() => expect(said).toEqual(["architect needs your input"]), { timeout: 4000 });
});

it("shows the paused pill after a reload until the page gets a click", () => {
  localStorage.setItem("rd.voice", "done");
  Object.defineProperty(navigator, "userActivation", { value: { hasBeenActive: false }, configurable: true });
  function Paused() {
    const voice = useVoice(idle, { say: async () => {}, chime: async () => {}, stop: () => {} });
    return voice.paused ? <VoicePausedPill /> : null;
  }
  render(<Paused />);
  expect(screen.getByRole("status")).toHaveTextContent("Voice paused. Click anywhere to resume.");
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("status")).toBeNull();
  Object.defineProperty(navigator, "userActivation", { value: undefined, configurable: true });
});

it("shows the paused pill when the browser refuses to speak", async () => {
  const speaker: Speaker = { say: async () => false, chime: async () => {}, stop: () => {} };
  function Refused({ sessions }: { sessions: VoiceSession[] }) {
    const voice = useVoice(sessions, speaker);
    return voice.paused ? <VoicePausedPill /> : null;
  }
  const view = render(<Refused sessions={idle} />);
  view.rerender(<Refused sessions={approval} />);
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Voice paused"), { timeout: 4000 });
});
