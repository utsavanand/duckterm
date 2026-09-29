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
the machine. Voice and rate are whatever the OS provides; expose a voice
picker only if the default sounds wrong.

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

- **The chime** is two soft sine notes, 660 Hz then 880 Hz, 0.14 s each at
  low volume, generated with Web Audio. There's no audio file to ship.
- **"is complete" stays bare in v1.** The progress digest behind a clause
  is written for reading, not listening, and often runs to a paragraph.
  A clause can come later, once there is a one-line summary worth speaking.
- **Where needs-you comes from.** Relay notes, not the waiting badge: notes
  already exclude offers and Codex's auto-reviewed requests, and they carry
  the note kind that decides the chime.
- **Where completions come from.** A session going from busy to idle. The
  announcement waits 45 s, because a turn that ends on a question becomes a
  needs-you note about 30 s after it stops (the relay's settle wait plus one
  classifier call). It's dropped if a note or new work arrives first.
- **One click.** Each spoken line shows a small toast with "Only needs-you",
  "Stop" and "Turn off". The level picker also sits in the header, next to
  Settings.
- **Typing.** Any keystroke in the dashboard holds announcements until 5 s
  after the last key. Typing in a terminal outside the dashboard isn't seen.
- **Persistence.** The level is stored under `rd.voice` in the browser's
  local storage and read back on load. Unknown or blocked storage falls back
  to the default.
- **Page load.** Notes already open when a dashboard loads are not read out.
  Only what happens from then on is spoken.
- **Browsers allow speech only after a user gesture.** Turning voice on
  speaks "Voice announcements on", and that click unlocks speech for the page.
  After a reload, the first announcement may be silent until the owner
  clicks or types in the dashboard once.

## Delivery

Visible and audible change, so: architect designs, owner reviews — for this
one a short recording or a live demo beats a screenshot — then ui-dev builds,
release-dev ships.
