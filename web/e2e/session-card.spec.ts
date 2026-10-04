import { expect, test } from "@playwright/test";
import { apiDelete, base, postEvent, seedSession } from "./helpers";

test("card actions stay accessible in every density; only stopped rows offer Resume with visible refusal", async ({ page }) => {
  const keys: string[] = [];
  try {
    for (const state of ["stopped", "interrupted", "terminated", "busy"]) {
      const key = await seedSession(`card-${state}`, {name:`Review ${state}`, runtime:"codex", launched:true, test:true});
      keys.push(key);
      if (state !== "busy") await postEvent({event_type:"Notification",session_key:key,lifecycle:state});
    }
    await page.addInitScript(() => localStorage.setItem("rd-theme", "dark"));
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(base());
    const stopped = page.locator(".rd-row",{has:page.getByText("Review stopped",{exact:true})});
    await expect(stopped.getByRole("button",{name:"Resume Review stopped",exact:true})).toBeVisible();
    for (const state of ["interrupted","terminated","busy"]) {
      await expect(page.locator(".rd-row",{has:page.getByText(`Review ${state}`,{exact:true})}).locator(".rd-row-resume")).toHaveCount(0);
    }
    await page.route(`**/sessions/${keys[0]}/resume`,route=>route.fulfill({status:409,json:{error:"cannot verify which conversation belongs to this session"}}));
    await stopped.getByRole("button",{name:"Resume Review stopped",exact:true}).click();
    await expect(page.getByText("Resume failed: cannot verify which conversation belongs to this session",{exact:true})).toBeVisible();
    await expect(stopped.getByRole("button",{name:"Resume Review stopped",exact:true})).toBeEnabled();
    await stopped.locator(".rd-row-name").click();
    const card=page.getByRole("region",{name:"Session controls"});
    for (const density of ["compact","standard","relaxed"]) {
      await page.getByRole("button",{name:"Settings",exact:true}).click();
      await page.getByRole("combobox",{name:"Sidebar density"}).selectOption(density);
      await page.keyboard.press("Escape");
      await expect(card.getByRole("button",{name:"Resume",exact:true})).toBeVisible();
      await expect(card.getByRole("button",{name:"Notes",exact:true})).toBeVisible();
      await expect(card.getByRole("button",{name:"Archive",exact:true})).toBeVisible();
      await expect(card.locator("summary")).toHaveCount(0);
      const deletion=card.locator(".rd-session-controls-danger").getByRole("button",{name:"Delete",exact:true});
      await expect(deletion).toBeVisible();
      const actionsBox=await card.locator(".rd-session-controls-actions").boundingBox();
      const deleteBox=await deletion.boundingBox();
      expect(deleteBox!.y).toBeGreaterThanOrEqual(actionsBox!.y+actionsBox!.height);
      await expect(page.locator(".rd-row-actions,.rd-density-actions")).toHaveCount(0);
      await expect(card.getByRole("button",{name:/Restart|Change model|Switch harness/})).toHaveCount(0);
    }
    await page.locator(".rd-row-name",{hasText:"Review busy"}).click();
    for (const name of ["Checkpoint","Notes","Stop","Archive","Delete"]) {
      await expect(card.getByRole("button",{name,exact:true})).toBeVisible();
    }
    await expect(card.getByRole("button",{name:"Resume",exact:true})).toHaveCount(0);
    await expect(card.getByRole("button",{name:"Fork",exact:true})).toHaveCount(0);
    await page.screenshot({path:"/tmp/duckterm-session-card.png"});
  } finally {for (const key of keys) await apiDelete(`/sessions/${key}`);}
});
