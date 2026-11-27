import { expect, test, type Locator } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base, expandFolder } from "./helpers";

// Model text in the review (chat, line answers, cards, diagrams) is written
// about a diff that can carry a prompt injection, so rendering it must fetch
// nothing: every remote-loading form below points at a reserved .invalid host
// and the test fails on any request to it (main-qa-2 and security on #509).
function git(cwd: string, ...args: string[]): string {
  return execFileSync("git", ["-c", "user.email=qa@duckterm.local", "-c", "user.name=QA", "-c", "commit.gpgsign=false", ...args], { cwd, encoding: "utf8" }).trim();
}

for(const modern of [false,true]) test(`model rendering ${modern ? "v2" : "legacy"}`,async({page})=>{
  test.setTimeout(60000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  const root = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-pr-list-")));
  const origin = join(root, "origin.git");
  const work = join(root, "work");
  git(root, "init", "-q", "--bare", origin);
  git(root, "init", "-q", "-b", "main", work);
  writeFileSync(join(work, "README.md"), "base\n");
  git(work, "add", "README.md");
  git(work, "commit", "-q", "-m", "initial");
  git(work, "remote", "add", "origin", origin);
  git(work, "push", "-q", "-u", "origin", "main");
  git(work, "-c", "tag.gpgsign=false", "-c", "tag.forceSignAnnotated=false", "tag", "-m", "first release", "v0.1.0");
  git(work, "checkout", "-q", "-b", "duckterm/e2e-review");
  writeFileSync(join(work, "feature.py"), "def feature():\n    return 1\n");
  git(work, "add", "feature.py");
  git(work, "commit", "-q", "-m", "add feature");
  git(work, "push", "-q", "-u", "origin", "duckterm/e2e-review");
  const folder = `Review probes ${Date.now()}`;
  let key = "";
  try {
    const launched = await apiPost("/sessions/launch", { command: "sh -c 'exec cat'", cwd: work, runtime: "generic", name: "pr-list-agent", in_terminal: false, test: true });
    expect(launched.status).toBe(200);
    key = String(launched.body.session_key);
    await apiPatch(`/sessions/${key}`, { group: folder });


    const reviewPath=`/review/${encodeURIComponent(work)}/${encodeURIComponent('duckterm/e2e-review')}`;
    const remote:string[]=[];
    await page.route('https://qa-render.invalid/**',async route=>{remote.push(route.request().url());await route.abort();});
    await page.route('**/edition',async route=>{const r=await route.fetch(),d=await r.json();d.features=d.features.filter((f:string)=>f!=='review_v2');if(modern)d.features.push('review_v2');await route.fulfill({response:r,json:d});});
    const prose=(where:string)=>`**Bold proof** and \`code proof\` with [link proof](https://example.invalid/docs).\n\n- first item\n- second item\n\n![ordinary image](https://qa-render.invalid/${where}/markdown)\n\n<img src="https://qa-render.invalid/${where}/html"><input type="image" src="https://qa-render.invalid/${where}/input"><p style="background:url(https://qa-render.invalid/${where}/style)">kept text</p><form action="https://qa-render.invalid/${where}/form"><button formaction="https://qa-render.invalid/${where}/button">b</button></form><math><mglyph src="https://qa-render.invalid/${where}/mglyph"></mglyph></math>\n\n\`\`\`mermaid\n%%{init: {"themeCSS": ".node rect{fill:url(https://qa-render.invalid/${where}/mermaid)}"}}%%\ngraph LR\n  A --> B\n\`\`\``;
    const exchange=(where:string)=>({q:'Synthetic question',a:prose(where),citations:[],hunk:null,files:[],at:1});
    await page.route(`**${reviewPath}/chat`,route=>route.fulfill({json:{messages:[exchange('chat')],suggestions:[]}}));
    await page.route(`**${reviewPath}/ask`,route=>route.fulfill({json:exchange('line')}));
    await page.route(`**${reviewPath}/cards`,route=>route.fulfill({json:{head_sha:'synthetic',stale:false,running:false,cards:[
      {id:'intent',number:1,title:'Intent',status:'ready',data:{summary:prose('intent'),claims:[],unclaimed:[],diagram:`%%{init: {"themeCSS": ".node rect{fill:url(https://qa-render.invalid/intent/diagram)}"}}%%\ngraph LR\n  A --> B`,verdict:'QA'}},
      {id:'blast',number:8,title:'Blast',status:'ready',data:{symbols:[],diagram:'',index:{base_files:1,head_files:1},limits:'',consequence:prose('blast'),risks:[],model_status:'ready',verdict:'QA'}},
      {id:'author',number:10,title:'Author',status:'ready',data:{trajectory:prose('trajectory'),verification:prose('verification'),events:[],turns:1,commits:1,pushes:0,verification_runs:[],abandoned_commands:[],owner_decisions:[],session:{key,name:'Synthetic',runtime:'generic'},model_status:'ready',verdict:'QA'}}
    ]}}));
    await page.goto(base());await expandFolder(page,folder);await page.locator('.rd-row-name',{hasText:'pr-list-agent'}).click();await page.getByRole('button',{name:'PRs',exact:true}).click();
    await page.getByRole('region',{name:'Pull requests'}).getByRole('row').filter({hasText:'duckterm/e2e-review'}).getByRole('button',{name:'Review',exact:true}).click();
    const workspace=page.getByRole('region',{name:'Review workspace'});await expect(workspace).toBeVisible();
    const formatting=async(box:Locator)=>{await expect(box.locator('strong')).toContainText(['Bold proof']);await expect(box.locator('code')).toContainText(['code proof']);await expect(box.getByRole('link',{name:'link proof'})).toHaveAttribute('href','https://example.invalid/docs');await expect(box.locator('li')).toContainText(['first item','second item']);};
    if(modern)await workspace.getByRole('button',{name:/Chat/}).click();
    await formatting(workspace.locator('.rd-chat-a').first());
    if(modern){
      await workspace.getByRole('tab',{name:/Files/}).click();await workspace.getByRole('button',{name:'Line 1',exact:true}).first().click();const input=workspace.getByRole('textbox',{name:'Question about the selected lines'});await input.fill('What changed?');await input.press('Enter');await formatting(workspace.locator('.rv2-answer > div').first());
    }else{
      await workspace.getByRole('tab',{name:'Review',exact:true}).click();
      for(const title of ['Intent','Blast','Author']){await workspace.getByRole('tab',{name:new RegExp(title)}).click();const boxes=workspace.locator('.rd-card-prose');for(let i=0;i<await boxes.count();i++)await formatting(boxes.nth(i));}
    }
    await page.waitForTimeout(150);
    expect(remote,'Model text must cause no remote resource requests').toEqual([]);
  } finally {
    if(key){await apiPost(`/sessions/${key}/stop`);await apiDelete(`/sessions/${key}`);}await apiDelete(`/folders/${encodeURIComponent(folder)}`);rmSync(root,{recursive:true,force:true});
  }
});
