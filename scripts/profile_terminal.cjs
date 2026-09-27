// Manual browser-load profile, not a timing assertion in CI.
// Build web/dist and serve it on loopback; all API and terminal traffic below
// is synthetic. No production sessions, tokens, PTYs, or agent CLIs are used.
// node scripts/profile_terminal.cjs /tmp/profile.json http://127.0.0.1:4424
const { chromium } = require('../web/node_modules/playwright');
const fs = require('node:fs');

async function profile() {
  const target = process.argv[3] || 'http://127.0.0.1:4424';
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(target).hostname)) {
    throw new Error('Serve the built dashboard on loopback');
  }
  const browser = await chromium.launch();
  const results = [];
  try {
    for (const count of [5, 15, 23]) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      const cdp = await page.context().newCDPSession(page);
      await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
      await page.route('**/*', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/' || path.startsWith('/assets/') || path.endsWith('.svg')) {
          return route.continue();
        }
        let data = { messages: [], pins: [], branches: [], approvals: [], rules: [], count: 0 };
        if (path === '/sessions') {
          data = { sessions: Array.from({ length: count }, (_, i) => ({
            session_key: `perf-${i}`, name: `perf-${i}`, test: true,
            state: 'idle', started_at: Date.now() - i, updated_at: Date.now(),
            event_count: 0, launched: 1, pty_owned: 1,
          })) };
        } else if (path === '/folders') data = { folders: [] };
        else if (path === '/connectors') data = { connectors: [] };
        else if (path === '/session-inbox-counts') data = { counts: {} };
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
      });
      await page.addInitScript(() => {
        window.__perf = { sockets: 0, bytes: 0, samples: [], pending: null, longTasks: [] };
        new PerformanceObserver(list => {
          for (const entry of list.getEntries()) window.__perf.longTasks.push(entry.duration);
        }).observe({ type: 'longtask', buffered: true });
        window.EventSource = class { close() {} };
        const encoder = new TextEncoder();
        class Socket {
          static OPEN = 1;
          readyState = 0;
          onopen = null;
          onmessage = null;
          onclose = null;
          constructor(url) {
            window.__perf.sockets++;
            setTimeout(() => {
              this.readyState = 1;
              this.onopen?.({});
              this.onmessage?.({ data: encoder.encode('READY\r\n').buffer });
              if (!url.includes('/perf-0/')) {
                this.timer = setInterval(() => {
                  const data = encoder.encode(
                    ('\x1b[32mAgent streaming output with tokens, files and progress\x1b[0m\r\n').repeat(64),
                  );
                  window.__perf.bytes += data.length;
                  this.onmessage?.({ data: data.buffer });
                }, 50);
              }
            }, 0);
          }
          send(data) {
            if (typeof data !== 'string') {
              setTimeout(() => this.onmessage?.({
                data: data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength),
              }), 0);
            }
          }
          close() {
            this.readyState = 3;
            clearInterval(this.timer);
            window.__perf.sockets--;
          }
        }
        window.WebSocket = Socket;
      });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(target);
      await page.locator('.rd-terminal-slot:visible .xterm-helper-textarea').waitFor();
      await page.waitForTimeout(1800);
      for (const key of ['perf-1', 'perf-2', 'perf-0']) {
        await page.getByText(key, { exact: true }).first().click();
        await page.waitForTimeout(200);
      }
      await page.evaluate(() => {
        const slot = [...document.querySelectorAll('.rd-terminal-slot')]
          .find(element => getComputedStyle(element).display !== 'none');
        const rows = slot.querySelector('.xterm-rows');
        document.addEventListener('keydown', event => {
          if (event.key === 'x') {
            window.__perf.pending = { start: performance.now(), length: rows.textContent.length };
          }
        }, true);
        new MutationObserver(() => {
          const pending = window.__perf.pending;
          if (pending && rows.textContent.length > pending.length) {
            window.__perf.pending = null;
            requestAnimationFrame(() => window.__perf.samples.push(performance.now() - pending.start));
          }
        }).observe(rows, { subtree: true, childList: true, characterData: true });
        window.__perf.longTasks = [];
      });
      await page.locator('.rd-terminal-slot:visible .xterm-helper-textarea').focus();
      for (let i = 0; i < 60; i++) {
        await page.keyboard.press('x');
        await page.waitForTimeout(100);
      }
      const measured = await page.evaluate(() => ({
        sockets: window.__perf.sockets, samples: window.__perf.samples,
        longTasks: window.__perf.longTasks,
      }));
      measured.samples.sort((a, b) => a - b);
      if (measured.samples.length !== 60 || errors.length) {
        throw new Error(`Incomplete profile: ${measured.samples.length} samples; ${errors.join('; ')}`);
      }
      results.push({
        count, sockets: measured.sockets, samples: measured.samples.length,
        p50: measured.samples[30], p95: measured.samples[57], max: measured.samples.at(-1),
        longTasks: measured.longTasks.length,
        longTaskMs: measured.longTasks.reduce((a, b) => a + b, 0), errors,
      });
      await page.close();
    }
  } finally { await browser.close(); }
  fs.writeFileSync(process.argv[2] || '/tmp/duckterm-terminal-profile.json', JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
}
profile().catch(error => { console.error(error); process.exitCode = 1; });
