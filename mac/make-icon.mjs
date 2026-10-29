// Render an approved edition SVG into its checked-in native icon. Normal
// builds only copy these resources; generation is an explicit maintenance step.
// Usage: node mac/make-icon.mjs --edition=lite|code|full [--test] [--output=PATH]
// Requires web/node_modules/playwright and its Chromium browser.
import {
  mkdtempSync,
  readFileSync,
  writeFileSync,
  rmSync,
  mkdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const here = dirname(fileURLToPath(import.meta.url));
const usage =
  "Usage: node mac/make-icon.mjs --edition=lite|code|full [--test] [--output=PATH]";
let edition;
let testBuild = false;
let output;
for (const arg of process.argv.slice(2)) {
  if (/^--edition=(lite|code|full)$/.test(arg) && edition === undefined) {
    edition = arg.slice("--edition=".length);
  } else if (arg === "--test" && !testBuild) {
    testBuild = true;
  } else if (
    arg.startsWith("--output=") &&
    arg.length > "--output=".length &&
    output === undefined
  ) {
    output = resolve(arg.slice("--output=".length));
  } else {
    console.error(`Invalid or duplicate argument: ${arg}\n${usage}`);
    process.exit(2);
  }
}
if (edition === undefined) {
  console.error(`Missing required --edition argument.\n${usage}`);
  process.exit(2);
}
const sourceName = edition === "full" ? "duckmark.svg" : `duckmark-${edition}.svg`;
const resourceName = `AppIcon${edition === "full" ? "" : edition[0].toUpperCase() + edition.slice(1)}${testBuild ? "Test" : ""}.icns`;
output ??= join(here, "Resources", resourceName);
let svg = readFileSync(join(here, "../web/src/assets", sourceName), "utf8");

if (testBuild) {
  // A purple duck plus a high-contrast badge distinguishes Test in the Dock.
  svg = svg.replaceAll("#FFD32B", "#D8B4FE").replaceAll("#F2BE0A", "#8B5CF6")
    .replaceAll("#E0AD00", "#6D28D9")
    .replace("</svg>", '<rect x="6" y="46" width="42" height="15" rx="5" fill="#F5F3FF"/><text x="27" y="57" text-anchor="middle" fill="#5B21B6" font-family="Arial,sans-serif" font-weight="900" font-size="11">TEST</text></svg>');
}

// iconutil expects exactly these names/sizes.
const sizes = [
  ["icon_16x16", 16],
  ["icon_16x16@2x", 32],
  ["icon_32x32", 32],
  ["icon_32x32@2x", 64],
  ["icon_128x128", 128],
  ["icon_128x128@2x", 256],
  ["icon_256x256", 256],
  ["icon_256x256@2x", 512],
  ["icon_512x512", 512],
  ["icon_512x512@2x", 1024],
];

const work = mkdtempSync(join(tmpdir(), "rdicon-"));
const iconset = join(work, "AppIcon.iconset");
mkdirSync(iconset);

let browser;
try {
  const { chromium } = await import("../web/node_modules/playwright/index.mjs");
  browser = await chromium.launch();
  const page = await browser.newPage();
  for (const [name, px] of sizes) {
    await page.setViewportSize({ width: px, height: px });
    await page.setContent(
      `<style>html,body{margin:0}svg{display:block}</style>` +
        svg
          .replace(/width="\d+"/, `width="${px}"`)
          .replace(/height="\d+"/, `height="${px}"`),
    );
    const el = await page.$("svg");
    const buf = await el.screenshot({ omitBackground: true });
    writeFileSync(join(iconset, `${name}.png`), buf);
  }
  const generated = join(work, "AppIcon.icns");
  execFileSync("iconutil", ["-c", "icns", iconset, "-o", generated]);
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, readFileSync(generated));
  console.log(`wrote ${output} from web/src/assets/${sourceName}${testBuild ? " (Test)" : ""}`);
} finally {
  await browser?.close();
  rmSync(work, { recursive: true, force: true });
}
