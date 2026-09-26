# Spike: is session state detection accurate, flaky, or harness-uneven?

Status: spike scoped 2026-09-25 at the owner's request. **Not yet run** —
this document is the question, the code reading that motivates it, and the
method. Implementation/investigation belongs to a dev session.

## Why this is worth a spike

State badges (busy / waiting / idle) are the product's core promise: "which
session needs you right now." If they are wrong, the dashboard is
actively misleading rather than merely incomplete. Two pieces of evidence
that the concern is real, not hypothetical:

- **v0.4.53 fixed live sessions falsely showing Interrupted/Resume** —
  startup reattached panes but never repaired stale interruption state.
  A whole class of sessions displayed the wrong lifecycle.
- **RETRO 2026-09-20**: Codex sessions showed an empty Messages tab because
  a per-harness capability was assumed rather than declared. Same shape of
  risk applies to state.

## There are TWO state systems, and that is the first thing to test

1. **Hook-driven** (`derive_state` in `persistence/history.py:160`) — the
   normal path for claude-code and codex. Events (SessionStart, tool use,
   permission request, Stop) drive transitions, with explicit lifecycle
   markers winning and at-rest states guarded against stray late events.
2. **Screen-scraping** (`detect_state` per harness in `runtimes/*.py`) — the
   fallback when hooks are absent or an agent has no hook system.

The `claude_code.py` docstring says detect_state "is only a fallback; the
hook adapter normally drives state." So the first question is not "is the
scraper accurate" but **which system is actually deciding, per harness, in
practice** — and whether they ever disagree. A session whose hooks are
misconfigured silently falls back to scraping, and nothing in the UI says so.

## Concrete defects visible from reading the code (pre-spike findings)

These need confirmation by test, but they are specific enough to state now.

### The four harnesses disagree on semantics AND on defaults

| Harness | Method | Default when no marker found |
| --- | --- | --- |
| claude-code | two substring checks | **busy** |
| codex | regex over reversed lines | **idle** |
| copilot | regex over reversed lines | **idle** |
| generic | `[idle]`/`[waiting]`/`[busy]` markers | **busy** |

An unrecognized screen means "busy" in two harnesses and "idle" in the other
two. That is not a tuning difference; it is opposite behavior for the same
evidence, and it decides whether a session appears to need you.

### claude-code's scraper keys on `"❯"`

```python
if "│ Do you want" in recent_output or "❯" in recent_output:
    return "waiting"
return "busy"
```
`❯` is Claude's input-prompt glyph — but it also appears in ordinary agent
output, in shell prompts inside the terminal, and in pasted text. Any
occurrence anywhere in the output window forces "waiting". And with no
marker at all the fallback is "busy", so a genuinely idle Claude session
that has scrolled its prompt out of the window reads as busy.

### copilot's regexes are dangerously generic

```python
_WORKING = re.compile(r"(working|thinking|running|generating)", re.IGNORECASE)
_WAITING = re.compile(r"(allow|approve|\(y/n\)|continue\?)", re.IGNORECASE)
```
These match ordinary English anywhere on a line. An agent that prints
"running tests", or a diff containing the word "approve", or a code comment
with "thinking", flips state. Worst case, an agent discussing its own
permissions ("I'll ask you to approve this") reports waiting when it is
working. This is the highest-suspicion item in the spike.

### Flakiness is structurally likely, not just possible

`detect_state` reads a **rolling decoded buffer** of recent output. So the
answer depends on how much has scrolled past — the same session state can
classify differently depending on window contents and timing. Two calls a
second apart can disagree with no state change at all. Any test that asserts
a single reading proves little; the spike must measure stability over time.

## What the spike should actually do

1. **Instrument, do not guess.** For every live session, log at intervals:
   the hook-derived state, the scraped state, which one the UI displayed, the
   harness, and whether hooks are installed. Run it across a normal working
   day with the real fleet (there are ~21 live terminals — a good sample).
2. **Measure disagreement rate** between the two systems per harness. Any
   nonzero rate where hooks are present is a bug in one of them.
3. **Measure flap rate**: how often a displayed state changes without a
   corresponding event. Flapping is the user-visible symptom of scraper
   instability.
4. **Adversarial inputs for the scrapers** — feed each `detect_state` output
   that *contains* its own trigger words in innocuous contexts: an agent
   printing "running tests", a diff containing "approve", a transcript
   quoting "❯", a code block with "(y/n)". Assert the classification is not
   fooled. These are cheap unit tests and are the deliverable most likely to
   catch a real bug today.
5. **Per-harness honesty check**: does each harness's declared capability
   match reality? Copilot and generic have no hook system, so they are
   always scraping — is that visible anywhere in the UI? (RETRO rule:
   "unsupported" must be visible, not indistinguishable from "no data".)
6. **Report, then decide.** The likely outcomes, in ascending cost: tighten
   the copilot/claude-code patterns; make the no-marker default consistent
   and documented; anchor scraping to the *last line* / prompt region rather
   than the whole window; surface "state is scraped, not hook-driven" in the
   UI. Do not rewrite the state machine before the data says which of these
   matters.

## Explicitly out of scope

Redesigning the state model, adding new states, or changing the hook
protocol. This is a measurement spike: find out whether the current
algorithms are accurate, flaky, and harness-even, and produce evidence that
names the specific fixes worth making.

## Deliverable

A short report answering, per harness: is it accurate, how often does it
flap, which system decides in practice, and what are the top three fixes —
plus the adversarial unit tests, which should land regardless of what the
report concludes.
