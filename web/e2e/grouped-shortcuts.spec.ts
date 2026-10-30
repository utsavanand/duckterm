import { expect, test, type Locator } from "@playwright/test";
import { apiDelete, apiPatch, apiPost, base, seedSession, expandFolder } from "./helpers";

test("grouped pins preserve names, drag rows and folders, and tolerate pointer drift", async ({page}) => {
  const keys=["group-pin-main","group-pin-product","group-pin-release","group-pin-long"];
  const folders=["Pin layout/core", "Pin website"];
  const labels=["main-dev","product","release-dev","folder-feature-development"];
  try {
    for(const name of folders) await apiPost("/folders",{name});
    const launch=await apiPost("/sessions/launch",{command:"sh -c 'echo READY; exec cat'",cwd:"/tmp",name:labels[0],test:true,in_terminal:false});
    expect(launch.status).toBe(200); keys[0]=String(launch.body.session_key);
    await apiPatch(`/sessions/${keys[0]}`,{group:folders[0]});
    for(let i=1;i<keys.length;i++) await seedSession(keys[i],{name:labels[i],group:folders[i===1?1:0],pinned:true});
    await page.addInitScript(order => { if (!localStorage.getItem("rd-pinned-shortcut-preferences-v1")) localStorage.setItem("rd-pinned-shortcut-preferences-v1",JSON.stringify({order,display:"expanded",colors:{[order[0]]:"Green",[order[2]]:"Red",[order[3]]:"Yellow"}})); }, keys);
    await page.goto(base()); await expandFolder(page,folders[0]);
    await page.locator('.rd-row-name',{hasText:labels[0]}).click();
    const rail=page.getByRole('navigation',{name:'Pinned session shortcuts'});
    await rail.getByRole("button",{name:"Choose sessions to pin"}).click();
    const picker=page.getByRole("dialog",{name:"Pin sessions"});
    for(const label of labels) await picker.getByRole("button",{name:`Pin ${label} in picker`,exact:true}).click();
    await page.keyboard.press("Escape");
    await expect(rail.locator('.rd-shortcut-group-heading')).toHaveCount(2);
    const names=()=>rail.locator('.rd-shortcut-session').allTextContents();
    await expect.poll(names).toEqual([labels[0],labels[2],labels[3],labels[1]]);
    const drag=async (source:Locator,target:Locator,after=false,edge=false)=>{
      const a=(await source.boundingBox())!,b=(await target.boundingBox())!;
      // Start on the rectangle's edge, outside both the grip and open button.
      await page.mouse.move(a.x+(edge?2:a.width/2),a.y+a.height/2); await page.mouse.down();
      await page.mouse.move(b.x+100,b.y+(after?b.height-3:3),{steps:12}); await page.mouse.up();
    };
    await drag(rail.locator('.rd-shortcut-tab').nth(2),rail.locator('.rd-shortcut-tab').first(),false,true);
    await expect.poll(names).toEqual([labels[3],labels[0],labels[2],labels[1]]);
    await expect(page.locator('.rd-row.selected .rd-row-name')).toHaveText(labels[0]);
    await drag(rail.locator('.rd-shortcut-group-heading').nth(1),rail.locator('.rd-shortcut-group').first());
    await expect.poll(names).toEqual([labels[1],labels[3],labels[0],labels[2]]);
    await page.reload(); await expect.poll(names).toEqual([labels[1],labels[3],labels[0],labels[2]]);
    const longRow=rail.locator('.rd-shortcut-tab').filter({has:page.getByRole('button',{name:`Open pinned ${labels[3]}`,exact:true})});
    const labelBounds=(await longRow.locator('.rd-shortcut-name').boundingBox())!;
    const colorBounds=(await longRow.locator('.rd-shortcut-color').boundingBox())!;
    expect(labelBounds.x+labelBounds.width).toBeLessThanOrEqual(colorBounds.x+1);
    for(const colorScheme of ['dark','light'] as const){
      await page.emulateMedia({colorScheme});
      await page.screenshot({path:`/tmp/grouped-shortcuts-${colorScheme}.png`});
    }
    await rail.getByRole('combobox').selectOption('hover');
    await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
    await rail.locator('.rd-shortcut-open').first().hover();
    const bounds=(await rail.boundingBox())!;
    await page.mouse.move(bounds.x-8,bounds.y+70);
    await page.waitForTimeout(850); // Deliberately exercise the grace timer beyond its deadline inside the margin.
    await expect(rail).toHaveClass(/is-hovered/);
    await page.mouse.move(100,700);
    await page.waitForTimeout(350);
    await expect(rail).toHaveClass(/is-hovered/);
    await expect(rail).not.toHaveClass(/is-hovered/);
  } finally {
    for(const key of keys) await apiDelete(`/sessions/${key}`);
    for(const name of [...folders].reverse().concat("Pin layout")) await apiDelete(`/folders/${encodeURIComponent(name)}`);
  }
});
