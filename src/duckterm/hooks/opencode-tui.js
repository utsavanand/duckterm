// DuckTerm managed plugin v1
// Route observation only: no prompt submission, keyboard simulation or approvals.
export default {
  id: "duckterm.identity.v1",
  async tui(api) {
    const endpoint = process.env.DUCKTERM_OPENCODE_OBSERVER;
    const token = process.env.DUCKTERM_OPENCODE_OBSERVER_TOKEN;
    const generation = process.env.DUCKTERM_HARNESS_GENERATION;
    if (!endpoint || !token || !generation || process.env.DUCKTERM_INTERNAL) return;
    let url;
    try { url = new URL(endpoint); } catch { return; }
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" ||
        url.username || url.password || url.search || url.hash) return;
    let pending = null;
    let sending = false;
    let stopped = false;
    let sequence = 0;
    let previous = "";
    let deliveredAt = 0;
    const flush = async () => {
      if (sending || stopped) return;
      sending = true;
      try {
        while (pending && !stopped) {
          const body = pending;
          pending = null;
          try {
            const response = await fetch(url, {
              method: "POST", headers: {"Content-Type": "application/json", "Authorization": `Bearer ${token}`},
              body, signal: AbortSignal.timeout(2000), redirect: "error",
            });
            if (response.ok) deliveredAt = Date.now();
          } catch { /* The next heartbeat reconciles observation; never block native hooks. */ }
        }
      } finally { sending = false; }
    };
    const poll = () => {
      if (stopped) return;
      try {
        const route = api.route.current;
        const state = {
          version: api.app.version, pid: process.pid, generation,
          directory: api.state.path.directory, ready: api.state.ready === true,
          route: route.name, sessionID: route.name === "session" ? route.params?.sessionID : null,
        };
        const serialized = JSON.stringify(state);
        if (serialized === previous && Date.now() - deliveredAt < 2000) return;
        previous = serialized;
        // Only the newest observation is useful; sequence gaps are explicit.
        pending = JSON.stringify({...state, sequence: ++sequence});
        if (pending.length > 8192) { pending = null; return; }
        void flush();
      } catch { /* Unknown native layouts never produce invented identity. */ }
    };
    const timer = setInterval(poll, 100);
    api.lifecycle.onDispose(() => { stopped = true; pending = null; clearInterval(timer); });
    poll();
  },
};
