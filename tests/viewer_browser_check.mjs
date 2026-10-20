#!/usr/bin/env node
/**
 * Load the viewer page in a real browser and render a receipt, so the DOM
 * layer is tested too — the JavaScript tests import the verifier and never
 * touch the page, which is exactly how a closure-scope bug ("parseJson is
 * not defined" reported as "not valid JSON") survived them.
 *
 *   node tests/viewer_browser_check.mjs <receipt.json> [keys.json]
 *
 * Prints a JSON summary: banner text, rendered row count, key panel state.
 * Requires playwright-core and a Chrome install; the Python test skips when
 * either is missing, because checking a receipt must not need a toolchain.
 */
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const [, , receiptPath, keysPath] = process.argv;
if (!receiptPath) {
  console.error("usage: node tests/viewer_browser_check.mjs <receipt.json> [keys.json]");
  process.exit(2);
}

let chromium;
try {
  ({ chromium } = require("playwright-core"));
} catch (e) {
  console.error("playwright-core is not installed");
  process.exit(3);
}

const pageUrl = pathToFileURL(path.resolve("docs/receipt_viewer.html")).href;
let browser;
try {
  browser = await chromium.launch({ channel: "chrome", headless: true });
} catch (e) {
  console.error(`cannot launch Chrome: ${e.message.split("\n")[0]}`);
  process.exit(3);   // “no browser here”, not “the page is broken”
}
const page = await browser.newPage({ viewport: { width: 1180, height: 900 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

try {
  await page.goto(pageUrl);
  const files = keysPath ? [receiptPath, keysPath] : [receiptPath];
  await page.setInputFiles("#file-receipt", files);
  await page.waitForSelector("#verdict:not([hidden])", { timeout: 5000 });
  await page.waitForTimeout(200);
  const state = await page.evaluate(() => ({
    banner: document.getElementById("banner").textContent.trim(),
    verified: document.getElementById("banner").classList.contains("ok"),
    checks: Array.from(document.querySelectorAll("#checks div"))
      .map((d) => d.textContent.trim()),
    rows: Array.from(document.querySelectorAll("#decision-table tr"))
      .map((r) => r.textContent.replace(/\s+/g, " ").trim()),
    keysHidden: document.getElementById("keys-panel").hidden,
    keysPin: document.getElementById("keys-pin").textContent,
    chainHidden: document.getElementById("chain-panel").hidden,
    decisionHidden: document.getElementById("decision").hidden,
  }));
  console.log(JSON.stringify({ ...state, consoleErrors: errors }, null, 1));
} finally {
  await browser.close();
}
