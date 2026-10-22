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
 *        [--fixtures DIR] [--rerecord] [--metrics-out DIR]
 *
 * Output (default docs/screenshots/):
 *   dash-<theme>-full.png          the whole page
 *   dash-<theme>-top.png           first viewport (the app shell + stat cards)
 *   dash-<theme>-<section>.png     one image per card, so a diff is legible
 *   layout-<theme>.json            element geometry + design tokens (--metrics-out)
 *
 * --fixtures freezes the data the page reads: every GET /api/** response is
 * saved under DIR on first capture and replayed from it afterwards. Without
 * that, the numbers change between captures and a pixel or geometry diff is
 * measuring the data, not the design. --rerecord refreshes the fixtures.
 *
 * Not part of the pytest suite: it needs a browser and a running server.
 * scripts/visual_check.py drives it and diffs the result.
 */
import { createRequire } from "node:module";
import crypto from "node:crypto";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const args = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i === -1 ? fallback : args[i + 1];
};
const flag = (name) => args.includes(`--${name}`);
const url = arg("url", "http://127.0.0.1:8100/");
const outDir = path.resolve(arg("out", "docs/screenshots"));
const fixturesDir = arg("fixtures", null);
const rerecord = flag("rerecord");
const metricsOut = arg("metrics-out", null);

// One file per endpoint+query, so a diff can name the request that changed.
const fixturePath = (requestUrl) => {
  const u = new URL(requestUrl);
  const digest = crypto.createHash("sha1")
    .update(u.pathname + u.search).digest("hex").slice(0, 8);
  const slug = u.pathname.replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "");
  return path.join(fixturesDir, `${slug}-${digest}.json`);
};

async function freezeApi(page, counts) {
  if (!fixturesDir) return;
  fs.mkdirSync(fixturesDir, { recursive: true });
  await page.route("**/api/**", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const file = fixturePath(route.request().url());
    if (!rerecord && fs.existsSync(file)) {
      counts.replayed++;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: fs.readFileSync(file, "utf-8"),
      });
    }
    const response = await route.fetch();
    const body = await response.text();
    fs.writeFileSync(file, body);
    counts.recorded++;
    return route.fulfill({ response, body });
  });
}

// Geometry and tokens, not text: a reordered card, a moved column or a
// changed palette is layout drift; a changed number is data (which the
// fixtures already froze, so it is not even that here).
async function layoutMetrics(page, theme) {
  return page.evaluate((name) => {
    const box = (el) => {
      const r = el.getBoundingClientRect();
      return [Math.round(r.x), Math.round(r.y + window.scrollY),
              Math.round(r.width), Math.round(r.height)];
    };
    const cards = Array.from(document.querySelectorAll(".card")).map((card) => ({
      title: (card.querySelector("h2") || {}).textContent?.trim() || "",
      box: box(card),
    }));
    const tokens = {};
    const style = getComputedStyle(document.documentElement);
    // The design system's semantic colours are --good/--warn/--bad
    // (docs/DESIGN_SYSTEM.md). This list once asked for --ok, which the
    // dashboard has never defined: the gate recorded an empty string for it
    // on both sides of every comparison and stayed permanently green while
    // the success colour drifted underneath it.
    for (const token of ["--bg", "--surface", "--surface-2", "--text", "--muted",
                         "--border", "--accent", "--good", "--bad", "--warn"]) {
      tokens[token] = style.getPropertyValue(token).trim();
    }
    return {
      theme: document.documentElement.getAttribute("data-theme") || name,
      viewport: [window.innerWidth, window.innerHeight],
      document: { w: document.documentElement.scrollWidth,
                  h: document.documentElement.scrollHeight },
      overflow_x: document.documentElement.scrollWidth > window.innerWidth,
      tokens,
      headings: Array.from(document.querySelectorAll("h2")).map((h) => h.textContent.trim()),
      cards,
    };
  }, theme);
}

// Every card heading on the page, matched by substring. Keep this list in
// step with the dashboard: a card added and not listed here is a card the
// visual gate never looks at.
const SECTIONS = [
  "Side A · Process Analyzer",
  "Side B · Agentic Pipeline",
  "Human-in-the-Loop",
  "Cost by Agent",
  "Dispositions",
  "Recent Audit Events",
  "LLM Budget",
  "Drift Canary",
  "Shadow mode",
  "Quality Gate",
  "A/B Cycle Time",
  "Market cost comparison",
  "Knowledge base",
  "Recent Ticket Runs",
  "Power BI feeds",
];

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-$/, "");

fs.mkdirSync(outDir, { recursive: true });
if (metricsOut) fs.mkdirSync(metricsOut, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const frozen = { recorded: 0, replayed: 0 };

for (const theme of ["dark", "bright"]) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  // Set before any script runs: the theme is resolved pre-paint, so this is
  // the only way to capture the other one without a flash of the wrong one.
  await ctx.addInitScript((t) => localStorage.setItem("aiops_theme", t), theme);
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  await freezeApi(page, frozen);   // counts are updated as requests arrive

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

  if (metricsOut) {
    const metrics = await layoutMetrics(page, theme);
    metrics.captured_at = new Date().toISOString();   // not part of a diff
    metrics.platform = process.platform;
    fs.writeFileSync(path.join(metricsOut, `layout-${theme}.json`),
                     JSON.stringify(metrics, null, 1));
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

if (fixturesDir) console.log(`api fixtures: ${frozen.recorded} recorded, ${frozen.replayed} replayed`);
await browser.close();
