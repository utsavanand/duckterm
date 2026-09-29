import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RelayNote } from "./api";
import { useVoice, VoiceMenu, VoiceToast } from "./VoiceControl";
import { Speaker, VoiceSession } from "./voice";

const relay = vi.hoisted(() => ({ notes: [] as RelayNote[] }));
vi.mock("./relay", () => ({ useRelay: () => ({ notes: relay.notes, rules: [], open: 0, refresh: () => {} }) }));
afterEach(() => { cleanup(); localStorage.clear(); relay.notes = []; });

const sessions: VoiceSession[] = [{ key: "a", label: "architect", group: "Duckterm", state: "idle" }];

function Harness({ speaker, notes }: { speaker: Speaker; notes: number }) {
  void notes; // re-render trigger
  const voice = useVoice(sessions, speaker);
  return (
    <>
      <VoiceMenu level={voice.level} onLevel={voice.setLevel} />
      {voice.spoken && <VoiceToast text={voice.spoken} level={voice.level} onLevel={voice.setLevel} onMute={voice.mute} onDismiss={voice.dismiss} />}
    </>
  );
}

it("speaks a new needs-you note, shows it, and drops to needs-you in one click that persists", async () => {
  const said: string[] = [];
  const speaker: Speaker = { say: async (t) => { said.push(t); }, chime: async () => {}, stop: () => {} };
  const view = render(<Harness speaker={speaker} notes={0} />);
  relay.notes = [{ id: "n1", session_key: "a", name: "architect", folder: "Duckterm", runtime: "codex", kind: "approval", status: "open", created_at: 1 }];
  view.rerender(<Harness speaker={speaker} notes={1} />);
  await waitFor(() => expect(said).toEqual(["architect needs your input"]), { timeout: 4000 });
  expect(screen.getByRole("status")).toHaveTextContent("architect needs your input");

  fireEvent.click(screen.getByRole("button", { name: "Only needs-you" }));
  expect(screen.getByLabelText("Voice announcements")).toHaveValue("needs");
  expect(localStorage.getItem("rd.voice")).toBe("needs");

  cleanup(); // a reload reads the stored choice back
  render(<Harness speaker={speaker} notes={2} />);
  expect(screen.getByLabelText("Voice announcements")).toHaveValue("needs");
});

it("turning voice off stops speech at once", async () => {
  const stop = vi.fn();
  const speaker: Speaker = { say: async () => {}, chime: async () => {}, stop };
  render(<Harness speaker={speaker} notes={0} />);
  await act(async () => {
    fireEvent.change(screen.getByLabelText("Voice announcements"), { target: { value: "off" } });
  });
  expect(stop).toHaveBeenCalled();
  expect(localStorage.getItem("rd.voice")).toBe("off");
});
