import {test,expect} from '@playwright/test';
import {apiPost,apiDelete} from './helpers';
test('context view toggles preserve terminal geometry at narrow and standard widths',async({page})=>{
 const r=await apiPost('/sessions/launch',{command:"python3 -u -c 'import sys; print(\"QA_GEOMETRY_READY\"); [print(s,flush=True) for s in sys.stdin]'",cwd:'/tmp',name:'qa147-geometry',in_terminal:false,test:true});expect(r.status).toBe(200);const key=String(r.body.session_key);
 try{
 await page.goto('/');await page.locator('.rd-row-name',{hasText:'qa147-geometry'}).click();await expect(page.locator('.xterm-screen').first()).toBeVisible();
 for(const width of [1440,1100,850,390]){
 await page.setViewportSize({width,height:900});
 const box=async()=>page.locator('.xterm-screen').first().boundingBox();
 await page.getByRole('tab',{name:'Session',exact:true}).click();await page.waitForTimeout(500);const before=await box();
 await page.getByRole('tab',{name:'Connectors',exact:true}).click();await expect(page.locator('.rd-connectors')).toBeVisible();await page.waitForTimeout(500);const after=await box();expect(after?.width).toEqual(before?.width);expect(after?.height).toEqual(before?.height);
 await page.getByRole('tab',{name:'Session',exact:true}).click();await page.waitForTimeout(500);const restored=await box();expect(restored?.width).toEqual(before?.width);expect(restored?.height).toEqual(before?.height);
 if(width===390)await page.screenshot({path:'/tmp/context-views-stacked.png'});
 }
 }finally{await apiDelete('/sessions/'+key)}
});
