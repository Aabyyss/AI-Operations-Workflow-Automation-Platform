#!/usr/bin/env node
/**
 * Capture the dashboard in both themes, as images you can actually look at.
 *
 * Why this exists: the in-app preview webview does not always composite
 * frames, and a DOM measurement is not a look. This drives a real Chrome
 * headless, pins the theme through localStorage before the first paint,
 * and writes one full-page image plus one image per card, per theme.
 *
 * Usage:
 *   python -m uvicorn backend.main:app --port 8100      # the server
 *   npm install --no-save playwright-core               # this one dependency
 *   node scripts/capture_dashboard.mjs [--url URL] [--out DIR]
 *
 * Output (default docs/screenshots/):
 *   dash-<theme>-full.png          the whole page
 *   dash-<theme>-top.png           first viewport (the app shell + stat cards)
 *   dash-<theme>-<section>.png     one image per card, so a diff is legible
 *
 * Not part of CI: it needs a browser and a running server. It is the manual
 * visual pass, kept as a script so it is repeatable rather than described.
 */
import { createRequire } from "node:module";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const args = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i === -1 ? fallback : args[i + 1];
};
const url = arg("url", "http://127.0.0.1:8100/");
const outDir = path.resolve(arg("out", "docs/screenshots"));

const SECTIONS = [
  "Human-in-the-Loop",
  "Cost by Agent",
  "Drift Canary",
  "Quality Gate",
  "A/B Cycle Time",
  "Market cost comparison",
  "Knowledge base",
  "Recent Ticket Runs",
  "Power BI feeds",
];

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-$/, "");

fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });

for (const theme of ["dark", "bright"]) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  // Set before any script runs: the theme is resolved pre-paint, so this is
  // the only way to capture the other one without a flash of the wrong one.
  await ctx.addInitScript((t) => localStorage.setItem("aiops_theme", t), theme);
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);

  await page.screenshot({ path: path.join(outDir, `dash-${theme}-top.png`) });
  await page.screenshot({ path: path.join(outDir, `dash-${theme}-full.png`), fullPage: true });

  for (const heading of SECTIONS) {
    const card = page.locator(".card", { has: page.locator(`h2:has-text("${heading}")`) }).first();
    if ((await card.count()) === 0) {
      console.warn(`  no card titled "${heading}"`);
      continue;
    }
    await card.scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    await card.screenshot({ path: path.join(outDir, `dash-${theme}-${slug(heading)}.png`) });
  }

  const state = await page.evaluate(() => ({
    theme: document.documentElement.getAttribute("data-theme"),
    bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
    pageOverflow: document.documentElement.scrollWidth > window.innerWidth,
  }));
  console.log(`${theme}: ${JSON.stringify(state)}` +
              (errors.length ? `  console errors: ${JSON.stringify(errors)}` : ""));
  await ctx.close();
}

await browser.close();
