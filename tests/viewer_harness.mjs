#!/usr/bin/env node
/**
 * Exercise the offline receipt viewer's script under node.
 *
 * The viewer (docs/receipt_viewer.html) must verify receipts with no
 * server, no Python and no libraries — so its logic cannot be tested by
 * importing the backend. This harness loads the inline script (extracted
 * from the HTML by tests/test_receipt_viewer.py) and drives it, which lets
 * the tests assert that the JavaScript agrees with Python, byte for byte:
 *
 *   node tests/viewer_harness.mjs script.js selftest
 *   node tests/viewer_harness.mjs script.js sha256 <file>
 *   node tests/viewer_harness.mjs script.js canonical <payload.json>
 *   node tests/viewer_harness.mjs script.js pynum <number-literal>
 *   node tests/viewer_harness.mjs script.js verify <receipt.json> [keys.json]
 *   node tests/viewer_harness.mjs script.js chain <bundle.json> [keys.json]
 *   node tests/viewer_harness.mjs script.js pubkey <seedhex>
 *   node tests/viewer_harness.mjs script.js sign <seedhex> <message-file>
 *   node tests/viewer_harness.mjs script.js verify-sig <pubhex> <sigfile> <msgfile>
 *
 * `selftest` compares the viewer's SHA-256/SHA-512 against node:crypto and
 * its Ed25519 against OpenSSL's — an independent implementation on both
 * sides, so a shared bug cannot pass.
 */
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const [, , scriptPath, command, ...args] = process.argv;
if (!scriptPath || !command) {
  console.error("usage: node viewer_harness.mjs <script.js> <command> [args…]");
  process.exit(2);
}
const V = require(path.resolve(scriptPath));

const readBytes = (p) => new Uint8Array(fs.readFileSync(p));
const readJson = (p) => V.parseJson(fs.readFileSync(p, "utf-8"));
const print = (o) => console.log(JSON.stringify(o, null, 1));

switch (command) {
  case "selftest": {
    let ok = true;
    const failures = [];
    for (const n of [0, 1, 31, 55, 56, 63, 64, 119, 127, 128, 200, 1000]) {
      const buf = crypto.randomBytes(n);
      if (V.sha256Hex(buf) !== crypto.createHash("sha256").update(buf).digest("hex")) {
        ok = false; failures.push(`sha256 @${n}`);
      }
      if (Buffer.from(V.sha512(buf)).toString("hex") !==
          crypto.createHash("sha512").update(buf).digest("hex")) {
        ok = false; failures.push(`sha512 @${n}`);
      }
    }
    // Ed25519: sign with the viewer's code, verify with OpenSSL's.
    for (let i = 0; i < 3; i++) {
      const seed = crypto.randomBytes(32);
      const msg = crypto.randomBytes(1 + i * 40);
      const sig = Buffer.from(V.edSign(seed, msg));
      const pub = Buffer.from(V.edPublicKey(seed));
      const spki = Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), pub]);
      const keyObj = crypto.createPublicKey({ key: spki, format: "der", type: "spki" });
      if (!crypto.verify(null, msg, keyObj, sig)) {
        ok = false; failures.push(`ed25519 sign @${i}`);
      }
      // …and the reverse direction: an OpenSSL-generated key signs, the
      // viewer verifies; the viewer signs with that same seed, OpenSSL
      // verifies. Both halves, or a shared bug could pass.
      const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
      const raw = new Uint8Array(
        privateKey.export({ format: "der", type: "pkcs8" }).subarray(16, 48));
      const osSig = crypto.sign(null, msg, privateKey);
      if (!V.edVerify(V.edPublicKey(raw), msg, new Uint8Array(osSig))) {
        ok = false; failures.push(`ed25519 verify @${i}`);
      }
      const osKeyObj = crypto.createPublicKey({
        key: publicKey.export({ format: "der", type: "spki" }),
        format: "der", type: "spki",
      });
      if (!crypto.verify(null, msg, osKeyObj, Buffer.from(V.edSign(raw, msg)))) {
        ok = false; failures.push(`ed25519 openssl-verify @${i}`);
      }
    }
    print({ ok, failures });
    process.exit(ok ? 0 : 1);
    break;
  }
  case "sha256":
    console.log(V.sha256Hex(readBytes(args[0])));
    break;
  case "canonical":
    process.stdout.write(V.canonical(V.parseJson(fs.readFileSync(args[0], "utf-8"))));
    break;
  case "pynum":
    console.log(V.pyNumber(args[0]));
    break;
  case "verify": {
    const trusted = args[1] ? V.trustedKeysFrom(readJson(args[1])) : null;
    try {
      print(V.verifyReceipt(readJson(args[0]), trusted));
    } catch (e) {
      print({ ok: false, reason: `not valid JSON: ${e.message}` });
      process.exit(1);
    }
    break;
  }
  case "chain": {
    try {
      const bundle = readJson(args[0]);
      const receipts = Array.isArray(bundle) ? bundle : bundle.receipts;
      const trusted = args[1] ? V.trustedKeysFrom(readJson(args[1])) : null;
      print(V.verifyChain(receipts, { trusted_keys: trusted }));
    } catch (e) {
      print({ ok: false, reason: `not valid JSON: ${e.message}` });
      process.exit(1);
    }
    break;
  }
  case "pubkey":
    console.log(Buffer.from(V.edPublicKey(Buffer.from(args[0], "hex"))).toString("hex"));
    break;
  case "sign": {
    const sig = V.edSign(Buffer.from(args[0], "hex"), readBytes(args[1]));
    console.log(Buffer.from(sig).toString("hex"));
    break;
  }
  case "verify-sig": {
    const ok = V.edVerify(Buffer.from(args[0], "hex"), readBytes(args[2]), readBytes(args[1]));
    print({ ok });
    process.exit(ok ? 0 : 1);
    break;
  }
  default:
    console.error(`unknown command: ${command}`);
    process.exit(2);
}
