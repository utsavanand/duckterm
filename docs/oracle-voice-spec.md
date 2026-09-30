# Oracle voice mode — spec

Status: built 2026-09-29 on branch `oracle-voice` (web/src/voice.ts,
web/src/VoiceControl.tsx), pending the owner's review by live demo.
Owner's ask: "voice mode, if that's turned on, Oracle will read or speak
something like session name dash needs your input or session name is
complete … Do we want those announcements to be really noisy or not noisy?
That can be configured."

## Summary

A voice toggle in Settings. When on, Oracle speaks short announcements —
"architect needs your input", "main-dev is complete" — so the owner can leave
the screen and still know which session wants them. How much it speaks is a
setting, because with 27 agents the same feature is either useful or
unbearable depending on the level.

This is a step toward the vision's level 3, working away from the keyboard
([pie-in-the-sky.md](pie-in-the-sky.md)), and the natural companion to
answering agents without typing (F11).

## Owner decisions (2026-09-29)

1. **Default level: Needs you + completions.** Both of the owner's examples
   are in the default — "<name> needs your input" and "<name> is complete".
   Noted risk, for the owner to judge in use: across 27 agents completions
   fire far more often than blocks, so if this proves noisy the fix is to
   drop to Needs-you rather than abandon voice. Make that one click.
2. **Approvals get a distinct sound.** A short chime before an approval
   announcement, so an approval is distinguishable without parsing words.
   Questions and completions share the plain voice.
3. **Re-announce once after 15 minutes** if a session is still blocked, then
   silence. Not configurable in v1 beyond off.
4. **Web layer, not native.** `window.speechSynthesis` in the dashboard, so
   it works in a browser tab and in the Mac app with no second notification
   path. Consequence to state plainly in the UI: **voice only speaks while a
   dashboard is open.** If the owner closes every window, nothing is spoken.

## What already exists

Most of the machinery is built; this is mostly a speaking layer on top.

| Piece | Where | Note |
| --- | --- | --- |
| The trigger | `App.tsx:212` fires `new Notification(\`${s.label} needs you\`)` on each newly-waiting session | Almost exactly the owner's wording, already debounced per session |
| Classification | `core/relay.py` labels each moment `blocked`, `offer`, or `none` | `blocked` measured correct 94% of the time over 104 endings |
| Needs-you list | `relay.needs_you()` excludes `offer` | The "quiet" level already exists as a concept |
| Completion | `Stop` events and progress digests | Feeds "is complete" |

No speech anywhere in the codebase today.

## Noise levels

One setting, three levels. **Default: Needs you + done** (owner decision 1).

| Level | Speaks | Roughly |
| --- | --- | --- |
| **Needs you** | Sessions genuinely blocked on the owner, plus approvals | A handful a day |
| **Needs you + done** (default) | Adds "<name> is complete" when a session finishes its work | The owner's chosen default |
| **Everything** | Adds offers — an agent finished and is suggesting more | Informative, near-constant at current fleet size |

Off is the fourth state: the toggle itself.

The cry-wolf risk is real — the owner's own Codex complaint was a genuine
question lost among false signals — so dropping a level must be one click
from wherever the voice is heard, not buried in Settings.

## Behaviour

- **What it says:** `<session name> needs your input`, `<session name> is
  complete`. Short, no jargon, no ids. Folder name only when two sessions
  share a name (several are called `main-dev`).
- **Pile-ups:** speak one at a time, never overlapping. If three or more
  arrive within a few seconds, say "three sessions need you" instead of
  reading each name.
- **Repeats:** never re-announce the same note. A still-blocked session is
  re-announced ONCE after 15 minutes, then silence (owner decision 3).
- **Quiet while typing:** do not speak while the owner is typing into a
  terminal — the supervisor already tracks owner keystrokes.
- **Mute:** a visible mute control, and speaking stops immediately when the
  toggle goes off, including anything queued.

## Implementation

`window.speechSynthesis` in the dashboard (owner decision 4) — built into the
browser and the Mac app's web view, no dependency, no network, nothing leaves
the machine. A voice picker is required, not optional (owner report, 2026-09-29: "really
robotic"). The owner's Mac had 41 English voices and no Enhanced or Premium
one, so the browser's first pick was a basic voice. Settings lists the
voices for the owner's language, best first and without macOS's novelty
voices, each with a Preview that speaks "architect needs your input". The
default prefers a Premium voice, then Enhanced, then Samantha. The list
fills when the browser's voiceschanged event fires, because the first call
can be empty. The choice is stored per device under `rd.voice.name`. When
no Enhanced or Premium voice is installed, Settings says where to download
one for free: System Settings, Accessibility, Spoken Content, then the
System voice menu, Manage Voices. On macOS 15.7 the labels "Spoken Content"
and "System voice" are confirmed from the system's own strings; "Manage
Voices" is Apple's documented menu item and was not read from this machine.
No cloud voices: announcements, including session names, stay on the
machine.

Deliberately NOT the Mac app's native notifier (`main.swift`), which would be
a second notification path — the app already double-notifies (B6). The cost
is that voice is silent when no dashboard is open, and the Settings copy must
say so rather than let the owner discover it.

The approval chime (owner decision 2) is a short generated tone or a small
bundled audio file, played before the announcement. Keep it quiet and brief;
it is a signal, not an alarm.

## Settings

Under Settings, next to desktop notifications:

- Voice announcements: off / Needs you / **Needs you + done (default)** /
  Everything
- Re-announcing is fixed at once-after-15-minutes in v1, not a setting.

**Persist the setting properly.** B6 is the open bug where the desktop
notification toggle does not persist — same menu, same class of mistake.
Voice must store its choice and read it back, or it will be reported as
broken the same way.

## Out of scope for v1

Speaking the content of a question (only that one exists — reading agent
output aloud is a different feature); voice input, which belongs with F11;
announcements on a phone, which needs Oracle on WhatsApp; per-session or
per-folder voice settings.

## Design choices made in the build

The architect's design ("Design — Oracle voice mode", 2026-09-29) is the
design; this spec is the requirements. As built:

- **One pure diff.** `announce(prev, next, level)` in web/src/voice.ts holds
  every rule. It compares two snapshots of sessions and relay notes, so there
  is no timer registry and no second state machine.
- **Needs you** comes from two sources, one for each kind of moment. A session
  that starts waiting (the fold's waiting state, which the desktop
  notification also uses) covers approvals and Claude menus. A relay
  `question` note covers a turn that ended by asking the owner: that turn
  never shows as waiting (Stop keeps a session busy, then idle), so the
  waiting diff alone would miss the owner's own "needs your input" example.
  A question isn't announced when its session is already waiting.
- **The chime** plays when the wait's cause is an approval (`waitingCause`,
  kept by the fold). It is two soft sine notes, 660 Hz then 880 Hz, from Web
  Audio, with no audio file.
- **Re-announce** is derived: a wait (from `waitingSince`) or a question
  (from the note's time) is said again once, as it crosses 15 minutes. A
  session that unblocks and blocks again gets a new wait time, so it is
  announced afresh.
- **"is complete"** is said 90 s after a turn ends, if the session hasn't
  started working again (`COMPLETION_SETTLE_MS`). That is longer than the
  ducks' 30 s settle (owner decision, 2026-09-30), because the relay needs
  about 30 s plus a model call to turn a turn that ended on a question into a
  needs-you note, which is announced instead. It is skipped while a question
  note is open for that session, and said bare, without a clause.
- **One click.** Each spoken line shows a toast with "Only needs-you",
  "Stop" and "Turn off". The level picker is also in the header and in
  Settings.
- **Typing** in a dashboard terminal holds announcements until 5 s after the
  last key. It counts this device's keystrokes only.
- **Persistence.** The level is stored per device under `rd.voice` in local
  storage and read back on load. Unknown or blocked storage falls back to the
  default.
- **Page load and first relay load** are not news. Nothing already open is
  read out.
- **Browsers allow speech only after a user gesture.** After a reload with
  voice on, a "Voice paused. Click anywhere to resume." pill shows until the
  first click or key. It also shows if the browser refuses to speak.

## Natural voice (Kokoro), optional

Owner decision, 2026-09-29: "all mac voices are quite bad"; cloud voices were
rejected ("open source"). Architect's ruling: an optional local component,
installed on request, without espeak. The macOS voice picker stays as the
fallback.

- **What:** Kokoro-82M (Apache-2.0 weights), int8 ONNX export, run through
  ONNX Runtime on the CPU of any Mac on macOS 13 or later, Intel included.
  The owner turned down a first MLX build at 785 MB ("just for voice it will
  be extra 1.4 gb?"). Measured on the owner's M4 Max, 2026-09-29:

  | Build | Installed | New phrase |
  | --- | --- | --- |
  | ONNX int8 (shipped) | 303 MB | about 1 s |
  | ONNX fp16 | about 400 MB | about 0.35 s |
  | MLX | 785 MB | about 0.15 s, Apple Silicon only |

  Cached repeats are instant in every build. An aligned log-mel distance from
  the MLX output was 0.45 for int8 and 0.47 for fp16, against 1.40 between
  two different voices, so no quality loss was measured. The dashboard warms
  the worker (about 11 s on the first cold start) when a natural voice is
  chosen, so the first announcement doesn't wait.
- **Install:** Settings, "Download natural voices (about 310 MB)", or
  `duckterm voice install`. The size is shown before anything downloads, and
  that click is the consent. It installs into
  `~/.duckterm/voice/{venv,model,cache}`, which is excluded from backups
  (storage class: reinstallable). `duckterm voice remove`, or Remove in
  Settings, deletes it.
- **Pinned:** every package in `src/duckterm/voice/requirements-arm64.lock`
  or `requirements-x86_64.lock`, installed with `--require-hashes
  --no-deps`. The two model files (from kokoro-onnx's GitHub release) and
  their SHA256s are in `kokoro-manifest.json`, checked while downloading.
  A file that doesn't match is deleted.
- **No GPL:** misaki's `[en]` extra pulls phonemizer-fork and espeak-ng
  (GPL-3), and so does the kokoro-onnx package, which imports them at load.
  Neither is used: the lock leaves the extra out, the worker calls ONNX
  Runtime directly, and the installer checks every installed package's
  license and fails closed on GPL. num2words (LGPL) is allowed.
- **Names:** without espeak, misaki drops unknown words ("main-dev" became
  "main"). `voice/names.py` keeps every word: separators become spaces, a
  few words get an inline pronunciation ("dev"), compounds split into known
  halves ("duckterm" becomes "duck term"), and anything else is spelled.
- **Server:** stays stdlib. It runs the worker as a subprocess speaking JSON
  lines, with a 15 s timeout per phrase and 60 s to start. The worker is
  killed on voice-off and on server shutdown. The phrase cache is capped at
  200 MB, oldest first.
- **Fallback, never silence:** the chosen Kokoro voice; otherwise the best
  macOS voice, with the reason shown in Settings; otherwise the browser's own
  refusal, shown as "Voice paused".

## Delivery

Visible and audible change, so: architect designs, owner reviews — for this
one a short recording or a live demo beats a screenshot — then ui-dev builds,
release-dev ships.
