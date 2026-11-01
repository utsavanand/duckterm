// Render the existing DuckTerm vector mark as an opaque, square iPhone asset.
// iOS applies its own icon mask; the source contains no rounded container.
import { chromium } from "../web/node_modules/playwright/index.mjs";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const output = join(here, "App/Assets.xcassets/AppIcon.appiconset");
mkdirSync(output, { recursive: true });
const svg = readFileSync(join(here, "../web/src/assets/duckmark.svg"), "utf8")
  .replace('<rect width="64" height="64" rx="14"', '<rect width="64" height="64" rx="0"')
  .replace('width="64" height="64"', 'width="1024" height="1024"');
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1024, height: 1024 } });
  await page.setContent('<style>html,body{margin:0;background:#0d0f14}svg{display:block}</style>' + svg);
  await page.screenshot({ path: join(output, "AppIcon.png") });
} finally {
  await browser.close();
}
writeFileSync(join(output, "Contents.json"), JSON.stringify({
  images: [{ filename: "AppIcon.png", idiom: "universal", platform: "ios", size: "1024x1024" }],
  info: { author: "xcode", version: 1 },
}, null, 2) + "\n");
