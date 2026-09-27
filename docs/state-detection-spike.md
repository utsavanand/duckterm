# Spike: is session state detection accurate, flaky, or harness-uneven?

Status: first reproducible defect investigated and fixed on 2026-09-26.
The original scoping notes below are historical hypotheses; corrections and
measured results take precedence. A full-day fleet study is still pending.

## First investigation: output without state evidence

Against main `e203926`, all ten isolated real-terminal probes reproduced a
false transition after a valid marker followed by `result: 42` and an ANSI
reset. Each case ran through both tmux and a raw PTY:

| Runtime | Real marker | Incorrect transition caused by ordinary output |
| --- | --- | --- |
| Codex | working | busy → idle |
| Copilot | thinking | busy → idle |
| Claude Code | permission menu | waiting → busy |
| Generic | idle | idle → busy |
| Generic | waiting | waiting → busy |

The fix makes `detect_state` return `None` when it has no evidence. Both
supervisor readers skip state emission in that case. `None` is an internal
absence of evidence, not a new user-visible session state. Explicit markers,
hook events and process-exit events retain their existing behavior. In
particular, silence or arbitrary text no longer proves that a Codex/Copilot
turn ended: a completion hook must report that, or a separately validated
idle-prompt reconciliation must establish it. Missing hooks can therefore
leave a stale state; inventing an idle event was not a reliable substitute.

The ten probes fail on unchanged main and pass with the fix. They use
`test=True`, an isolated home and tmux socket, and stop their processes.
Existing runtime and history-transition tests also pass. These deterministic
results establish the defect, not a production-wide accuracy or flap rate.

### Corrections to the initial theory

- Copilot **has hooks**, declared by `CopilotRuntime.hook_spec`; only generic
  has no hook specification. A declared spec does not prove hook delivery.
- The supervisor calls `detect_state` on individual decoded lines, not a
  rolling screen. The tmux reader can additionally split a line at a 4096-byte
  read boundary. Separately, `/sessions` reconciles waiting states from an
  eight-line visible-screen snapshot.
- Hook and output events both reach the same event bus and `HistoryStore`.
  There is no general rule giving hooks precedence. Consequently disagreement
  alone is not ground truth, and hook/output provenance is not reliably
  distinguishable in old event records.

### Remaining investigation priorities

1. Tighten positive matches using captured real prompt fixtures: Copilot's
   bare `approve`/`running`, Claude's embedded `❯`, and Codex's broad working
   phrases still match ordinary prose. This fix changes only the no-evidence
   case; it does not claim those false positives are solved.
2. Measure hook delivery and output-event provenance, including after restart.
   The supervisor's cached state is not synchronized with every hook event;
   explicit output transitions can still disagree with the persisted state.
3. Sample state transitions over a working day with ground-truth observations.
   Measure per-harness flapping and distinguish missing hooks from detector
   errors before changing precedence or adding UI indicators.

No live terminal contents or credentials are included in this report.

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
