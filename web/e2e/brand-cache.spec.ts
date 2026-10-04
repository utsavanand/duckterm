import { expect, test } from "@playwright/test";
import { createServer, request } from "node:http";
import type { AddressInfo } from "node:net";

// Routing in Playwright disables the HTTP cache. Use a real HTTP proxy to keep
// the stale response cached while loading the new build in the same browser.
test("brand bypasses a previously immutable green favicon", async ({ page, baseURL }) => {
  let oldIconRequests = 0;
  const proxy = createServer((req, res) => {
    if (req.url === "/old-brand-cache") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end('<html><head></head><body><img id="old" src="/favicon.svg"></body></html>');
      return;
    }
    if (req.url === "/favicon.svg") {
      oldIconRequests++;
      res.writeHead(200, {
        "Content-Type": "image/svg+xml",
        "Cache-Control": "public, max-age=31536000, immutable",
      });
      res.end('<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22"><rect width="22" height="22" fill="#2ac58c"/></svg>');
      return;
    }
    const upstream = request(new URL(req.url!, baseURL), {
      method: req.method, headers: req.headers,
    }, response => {
      res.writeHead(response.statusCode!, response.headers);
      response.pipe(res);
      res.on("close", () => response.destroy());
    });
    upstream.on("error", () => { res.writeHead(502); res.end(); });
    req.pipe(upstream);
  });
  await new Promise<void>(resolve => proxy.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${(proxy.address() as AddressInfo).port}`;
  try {
    await page.goto(`${origin}/old-brand-cache`);
    await expect(page.locator("#old")).toHaveJSProperty("naturalWidth", 22);
    const cached = await page.evaluate(() => fetch("/favicon.svg").then(r => r.text()));
    expect(cached).toContain("#2ac58c");
    expect(oldIconRequests).toBe(1);
    await page.goto(origin);
    const mark = page.locator(".rd-brand-mark");
    await expect(mark).toBeVisible();
    const source = await mark.getAttribute("src");
    expect(source).toMatch(/^\/assets\/duckmark-[\w-]+\.svg$/);
    await expect(mark).toHaveJSProperty("complete", true);
    expect(await mark.evaluate(img => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    await expect(page.locator('link[rel="icon"][type="image/svg+xml"]')).toHaveAttribute("href", source!);
    const response = await page.request.get(`${baseURL}${source}`);
    expect(response.headers()["cache-control"]).toBe("public, max-age=31536000, immutable");
    expect(await response.text()).toContain("#FFD32B");
    const legacy = await page.request.get(`${baseURL}/favicon.svg`);
    expect(legacy.headers()["cache-control"]).toBe("no-cache");
    expect(await legacy.text()).toBe(await response.text());
    expect(oldIconRequests).toBe(1);
  } finally {
    await page.goto("about:blank");
    proxy.closeAllConnections();
    await new Promise<void>((resolve, reject) => proxy.close(error => error ? reject(error) : resolve()));
  }
});
