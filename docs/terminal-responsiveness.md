# Terminal responsiveness at fleet scale

B9 implementation on `fix/terminal-responsiveness`; release pending.
Based on Architect's `performance-investigation.md`.

The single-session dashboard previously mounted every owned terminal, even
before it was visited. Hidden xterm parsers consumed background output on the
same browser thread that handles typing. At 23 sessions, 23 parsers remained
connected. CSS hiding did not suspend parsing.

The dashboard now mounts only the selected terminal and up to two recently
visited terminals. Revisiting a warm view does not reconnect. Older views close
only their browser subscription; their server-owned PTYs keep running. Selecting
an evicted view reconnects through the existing attach snapshot. Focus and folder
grids continue to display all their chosen panes. Terminal rendering is memoized,
and the session list keeps its identity between actual data changes.

## Reproducible profile

Build the dashboard and serve only its static assets on a dedicated loopback port:

```sh
npm run build --prefix web
python3 -m http.server 4424 --bind 127.0.0.1 --directory web/dist
# In another terminal:
node scripts/profile_terminal.cjs /tmp/profile.json http://127.0.0.1:4424
```

The script replaces every API response and terminal connection with synthetic
fixtures. It creates no server sessions, processes, credentials or persisted data.
It runs Chromium at 1440×1000 with 4× CPU throttling. Each background connection
receives 64 ANSI-colored lines every 50 ms. Three terminal views are visited to
warm the cache before 60 foreground keystrokes per fleet size, 100 ms apart.

The measurement starts on browser keydown and ends at the next animation-frame
callback after the echoed character changes xterm's DOM. It is a browser
key-to-render proxy, not a native Mac display or network round-trip measurement.
The fake echo excludes real PTY and transport delays. CI checks behavior, not
wall-clock timing; the manual profile reports timing separately.

Baseline: main `1f7fea6`, same host and browser workload.

| Sessions | Baseline connected views | Baseline p50 / p95 (ms) |
| --- | --- | --- |
| 5 | 5 | 22.8 / 26.7 |
| 15 | 15 | 37.1 / 51.2 |
| 23 | 23 | 42.1 / 70.5 |

| Sessions | Changed connected views | Changed p50 / p95 (ms) |
| --- | --- | --- |
| 5 | 3 | 21.4 / 25.9 |
| 15 | 3 | 21.4 / 26.0 |
| 23 | 3 | 21.8 / 25.7 |

At 23 sessions, p95 fell about 64%. The measured typing interval had 12
long tasks totaling 637 ms before and none after. All 60 samples were captured
at each size, with no browser errors. An additional after-change run gave
25.5 / 25.5 / 26.1 ms p95, consistent with the final run above.
These are controlled local results; installed native acceptance remains required.

## Regression coverage and limits

A real isolated PTY test types an unfinished command into `cat`, visits enough
sessions to evict its browser view, reconnects, continues the draft and submits
it. The same full line echoes back twice, proving the PTY retained the draft.
The test also checks the three-view cap and that revisiting a recent view does
not reconnect. Existing browser tests cover Focus input, grids, side-panel
collapse, paste, theme, replay, manual scrollback and focus around menus.

An evicted terminal has the same replay limits as an ordinary reconnect:
server capture supplies its existing history window, not the former browser's
entire 5,000-line buffer. PTY-side input drafts survive; browser-only selections
and search state are not retained across eviction. No transcripts, artifacts,
backups or session history are deleted. No new controls or layout are added.

The global timestamp tick, document-visibility polling and server per-row caching are
separate follow-ups if native measurements still show a bottleneck. This change
addresses hidden parsers and avoidable terminal reconciliation first.

UI review clarified that Messages and Inbox already unmount when their tabs
are hidden, stopping their panel timers. That is not an outstanding hidden-panel
polling defect; document/background visibility is a separate consideration.
