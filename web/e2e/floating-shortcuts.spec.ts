import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base } from "./helpers";

test("floating shortcuts preserve geometry, drafts and connections while sharing persisted pins", async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1512, height: 982 });
  const keys: string[] = [], sizes: unknown[] = [];
  let connections = 0;
  page.on("websocket", socket => {
    if (!keys.some(key => socket.url().includes(`/sessions/${key}/terminal`))) return;
    connections++;
    socket.on("framesent", ({ payload }) => {
      try { const data = JSON.parse(String(payload)); if (data.resize) sizes.push(data.resize); } catch { /* Keystrokes. */ }
    });
  });
  try {
    for (const name of ["shortcut-ui", "shortcut-api", "shortcut-qa", "shortcut-extra"]) {
      const result = await apiPost("/sessions/launch", { command: `sh -c 'echo READY_${name}; exec cat'`, cwd: "/tmp", name, in_terminal: false, test: true });
      expect(result.status).toBe(200); keys.push(String(result.body.session_key));
    }
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "shortcut-ui" }).click();
    const rows = page.locator(".rd-terminal-slot:visible .xterm-rows");
    const slot = page.locator(".rd-terminal-slot:visible");
    const rail = page.getByRole("navigation", { name: "Pinned session shortcuts" });
    const width = async () => (await slot.boundingBox())!.width;
    await expect(rows).toContainText("READY_shortcut-ui");
    await expect.poll(() => sizes.length).toBeGreaterThan(0);
    const originalWidth = await width(), initialResizes = sizes.length, initialConnections = connections;
    await page.locator(".rd-terminal-slot:visible .xterm-helper-textarea").focus();
    await page.keyboard.type("UNSENT_SHORTCUT_DRAFT");
    // Use the collapsed target, not locator.click() which may chase layout shifts.
    await page.mouse.move(500, 700);
    await expect.poll(async () => (await rail.boundingBox())!.width).toBe(38);
    const firstPin = rail.getByRole("button", { name: "Pin current session shortcut-ui" });
    const beforeHover = (await firstPin.boundingBox())!;
    const firstPoint = { x: beforeHover.x + beforeHover.width / 2, y: beforeHover.y + beforeHover.height / 2 };
    await page.mouse.move(firstPoint.x, firstPoint.y);
    await expect.poll(async () => (await rail.boundingBox())!.width).toBe(260);
    expect((await firstPin.boundingBox())!.y).toBe(beforeHover.y);
    expect(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest("button")?.getAttribute("aria-label"), firstPoint)).toBe("Pin current session shortcut-ui");
    await page.screenshot({ path: "/tmp/floating-shortcuts-first-pin.png" });
    await page.mouse.click(firstPoint.x, firstPoint.y);
    await expect(rail.getByRole("button", { name: "Open pinned shortcut-ui" })).toBeVisible();
    await expect(page.locator(".rd-agents").getByRole("button", { name: "Unpin shortcut-ui", exact: true })).toBeVisible();
    await rail.getByRole("button", { name: "Choose sessions to pin" }).click();
    const picker = page.getByRole("dialog", { name: "Pin sessions" });
    await picker.getByRole("searchbox").fill("shortcut-");
    await picker.getByRole("button", { name: "Pin shortcut-api in picker" }).click();
    await expect(picker.getByRole("button", { name: "Unpin shortcut-api in picker" })).toBeEnabled();
    await picker.getByRole("button", { name: "Pin shortcut-qa in picker" }).click();
    await expect(picker.getByRole("button", { name: "Pin shortcut-extra in picker" })).toBeEnabled();
    await page.screenshot({ path: "/tmp/floating-shortcuts-picker.png" });
    await page.keyboard.press("Escape");
    await expect(picker).toBeHidden();
    await expect(rail.getByRole("button", { name: "Choose sessions to pin" })).toBeFocused();
    expect(await width()).toBe(originalWidth); expect(sizes.length).toBe(initialResizes);
    const overlap = await slot.evaluate(element => {
      const term = element.querySelector(".xterm")!.getBoundingClientRect();
      const tabs = element.querySelectorAll(".rd-shortcut-tab");
      const a = tabs[0].getBoundingClientRect(), b = tabs[1].getBoundingClientRect();
      const gap = document.elementFromPoint(a.x + 15, (a.bottom + b.top) / 2);
      return { overlap: term.right - a.left, gapPasses: !!gap?.closest(".xterm") };
    });
    expect(overlap.overlap).toBeGreaterThan(100); expect(overlap.gapPasses).toBe(true);
    // Compact buttons expand leftward, leaving the original click target intact.
    const railWidth = async () => (await rail.boundingBox())!.width;
    await page.locator(".rd-terminal-slot:visible .xterm-helper-textarea").focus();
    await page.mouse.move(500, 700);
    await expect.poll(railWidth).toBe(38);
    await page.screenshot({ path: "/tmp/floating-shortcuts-compact.png" });
    const compact = (await rail.getByRole("button", { name: "Open pinned shortcut-ui" }).boundingBox())!;
    const point = { x: compact.x + compact.width / 2, y: compact.y + compact.height / 2 };
    await page.mouse.move(point.x, point.y);
    await expect.poll(railWidth).toBe(260);
    expect(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest("button")?.getAttribute("aria-label"), point)).toBe("Open pinned shortcut-ui");
    await page.mouse.click(point.x, point.y);
    await expect(rail.locator(".rd-shortcut-tab")).toHaveCount(3);
    expect(await width()).toBe(originalWidth); expect(sizes.length).toBe(initialResizes);
    await page.mouse.move(500, 700);
    await expect.poll(railWidth).toBe(38);
    await page.keyboard.press("Tab");
    await rail.getByRole("button", { name: "Choose sessions to pin" }).focus();
    await expect.poll(railWidth).toBe(260);
    await page.keyboard.press("Enter");
    await expect(picker).toBeVisible();
    await page.mouse.move(500, 700);
    await expect.poll(railWidth).toBe(260);
    await page.keyboard.press("Escape");
    await rail.getByRole("button", { name: "Open pinned shortcut-api" }).click();
    await expect(rows).toContainText("READY_shortcut-api");
    await rail.getByRole("button", { name: "Open pinned shortcut-ui" }).click();
    await expect(rows).toContainText("UNSENT_SHORTCUT_DRAFT"); expect(connections).toBe(initialConnections + 1);
    await rail.getByRole("button", { name: "Open pinned shortcut-ui" }).hover();
    await rail.getByRole("button", { name: "Unpin shortcut-ui from shortcuts" }).click();
    await expect(rail.getByRole("button", { name: "Pin current session shortcut-ui" })).toBeEnabled();
    await expect(page.locator(".rd-row.selected .rd-row-name")).toHaveText("shortcut-ui");
    await rail.getByRole("button", { name: "Pin current session shortcut-ui" }).click();
    await expect(rail.getByRole("button", { name: "Open pinned shortcut-ui" })).toBeVisible();
    // Session switches above legitimately fitted the new terminal; measure only customization below.
    const customizationResizes = sizes.length;
    // Personal presentation must not touch pin membership, selected terminal or its connection.
    const uiTab = rail.locator(".rd-shortcut-tab").filter({ has: page.getByRole("button", { name: "Open pinned shortcut-ui", exact: true }) });
    await expect(uiTab.locator(".rd-shortcut-folder")).toHaveText("Ungrouped");
    await uiTab.getByRole("button", { name: /^Color for/ }).click();
    await page.getByRole("dialog", { name: "Shortcut color", exact: true }).getByRole("button", { name: "Red", exact: true }).click();
    await expect(uiTab.getByRole("button", { name: /Color for .*: Red$/ })).toBeFocused();
    await expect(uiTab).toHaveCSS("border-left-color", "rgb(237, 120, 120)");
    await rail.getByRole("combobox", { name: "Shortcut display" }).selectOption("expanded");
    await page.locator(".rd-terminal-slot:visible .xterm-helper-textarea").focus();
    await page.mouse.move(500, 700);
    await expect.poll(railWidth).toBe(260);
    const tabNames = () => rail.locator(".rd-shortcut-session").allTextContents();
    const originalOrder = await tabNames();
    // Uncolored pins must remain opaque over terminal text.
    for (const tab of await rail.locator(".rd-shortcut-tab").all()) {
      expect(await tab.evaluate(el => getComputedStyle(el).backgroundColor)).not.toMatch(/\/\s*0\.|rgba\([^)]*,\s*0\./);
    }
    await rail.locator(".rd-shortcut-grip").first().click();
    await expect(page.getByRole("dialog", { name: "Move shortcut", exact: true })).toHaveCount(0);
    await page.keyboard.press("Alt+ArrowDown");
    await expect.poll(async () => (await tabNames())[1]).toBe(originalOrder[0]);
    await page.keyboard.press("Alt+ArrowUp");
    await expect.poll(tabNames).toEqual(originalOrder);
    const dragFrom = await rail.locator(".rd-shortcut-open").first().boundingBox();
    const dragTo = await rail.locator(".rd-shortcut-open").nth(2).boundingBox();
    expect(dragFrom && dragTo).toBeTruthy();
    await page.mouse.move(dragFrom!.x + 25, dragFrom!.y + 18); await page.mouse.down();
    await page.mouse.move(dragTo!.x + 25, dragTo!.y + 28, { steps: 12 }); await page.mouse.up();
    await expect.poll(async () => (await tabNames())[2]).toBe(originalOrder[0]);
    const savedOrder = await tabNames();
    // Escape and a drop outside the rail cancel without selecting a session.
    const from = await rail.locator(".rd-shortcut-open").first().boundingBox();
    const to = await rail.locator(".rd-shortcut-open").nth(2).boundingBox();
    for (const cancel of ["escape", "outside"]) {
      await page.mouse.move(from!.x + 25, from!.y + 18); await page.mouse.down();
      await page.mouse.move(cancel === "outside" ? from!.x - 100 : to!.x + 25, to!.y + 28, { steps: 10 });
      if (cancel === "escape") await page.keyboard.press("Escape");
      await page.mouse.up(); await expect.poll(tabNames).toEqual(savedOrder);
      await expect(rail.locator(".dragging,.drop-before,.drop-after")).toHaveCount(0);
    }
    expect(await width()).toBe(originalWidth); expect(sizes.length).toBe(customizationResizes);
    expect(connections).toBe(initialConnections + 1);
    await expect(page.locator(".rd-row.selected .rd-row-name")).toHaveText("shortcut-ui");
    await page.screenshot({ path: "/tmp/floating-shortcuts-customized.png" });
    for (const theme of ["dark", "light"] as const) {
      await page.emulateMedia({ colorScheme: theme });
      await page.screenshot({ path: `/tmp/floating-shortcuts-${theme}.png` });
    }
    await page.getByRole("button", { name: "Collapse Context panel", exact: true }).click();
    await expect.poll(width).toBeGreaterThan(originalWidth);
    await expect(rail).toBeVisible();
    await expect(page.getByRole("button", { name: "Show Context panel", exact: true })).toBeVisible();
    await page.screenshot({ path: "/tmp/floating-shortcuts-collapsed.png" });
    await page.getByRole("button", { name: "Show Context panel", exact: true }).click();
    await expect.poll(width).toBe(originalWidth);
    await expect(rows).toContainText("UNSENT_SHORTCUT_DRAFT"); expect(connections).toBe(initialConnections + 1);
    await page.reload();
    await page.locator(".rd-row-name", { hasText: "shortcut-ui" }).click();
    await expect(rail.locator(".rd-shortcut-tab")).toHaveCount(3);
    await expect(page.getByRole("button", { name: "Focus · 3", exact: true })).toBeVisible();
    await expect(rows).toContainText("UNSENT_SHORTCUT_DRAFT");
    await expect.poll(tabNames).toEqual(savedOrder);
    await expect(rail.getByRole("combobox", { name: "Shortcut display" })).toHaveValue("expanded");
    await expect(uiTab.getByRole("button", { name: /Color for .*: Red$/ })).toBeVisible();
    await rail.getByRole("combobox", { name: "Shortcut display" }).selectOption("hover");
    await page.locator(".rd-terminal-slot:visible .xterm-helper-textarea").focus();
    await page.mouse.move(500, 700);
    await expect.poll(railWidth).toBe(38);
    await page.setViewportSize({ width: 600, height: 900 });
    await rail.getByRole("button", { name: "Choose sessions to pin" }).click();
    await expect(picker).toBeVisible();
    const box = (await picker.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(600);
    expect(box.y + box.height).toBeLessThanOrEqual(900);
    await page.screenshot({ path: "/tmp/floating-shortcuts-narrow.png" });
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(await rail.evaluate(element => getComputedStyle(element).transitionDuration)).toBe("0s");
  } finally {
    for (const key of keys) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
  }
});
