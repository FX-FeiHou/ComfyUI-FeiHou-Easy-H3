// Run with NODE_PATH containing playwright, and an explicit sample HTML path.
// A fresh headless browser is used; no user tabs, accounts or saved data.
import { createRequire } from "node:module";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const parserPath = fileURLToPath(new URL("../web/production_pack_parser.js", import.meta.url));
const html = fs.readFileSync(process.argv[2], "utf8");
const server = http.createServer((req, res) => {
    if (req.url === "/parser.js") {
        res.setHeader("Content-Type", "text/javascript");
        res.end(fs.readFileSync(parserPath));
    } else if (req.url === "/sample") {
        res.setHeader("Content-Type", "text/plain; charset=utf-8");
        res.end(html);
    } else res.end('<html><body><script type="module">import {renderShotlist} from "/parser.js"; window.renderShotlist=renderShotlist;</script></body></html>');
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ headless: true, ...(process.env.H3_TEST_BROWSER ? { executablePath: process.env.H3_TEST_BROWSER } : {}) });
try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForFunction(() => typeof window.renderShotlist === "function");
    const shots = await page.evaluate(async () => window.renderShotlist(await (await fetch("/sample")).text()));
    if (shots.length !== 36) throw new Error(`Expected final 36 shots, got ${shots.length}`);
    if (shots[3].range !== "00:20:900–00:25:700") throw new Error("Wrong fourth shot");
    if (shots.some((s) => !s.prompt_en || !s.prompt_zh || s.refs.length > 9)) throw new Error("Missing bilingual prompts / wrong references");
    console.log(JSON.stringify({ total: shots.length, first: { ...shots[0], prompt_en: shots[0].prompt_en.slice(0, 180), prompt_zh: shots[0].prompt_zh.slice(0, 80) }, last: { id: shots.at(-1).id, range: shots.at(-1).range, refs: shots.at(-1).refs } }, null, 2));
    if (process.argv[3]) fs.writeFileSync(path.resolve(process.argv[3]), JSON.stringify(shots, null, 2));
    const isolated = await page.evaluate(async () => {
        const html = `<article class="unit"><h2>test</h2></article><script>try{parent.document.body.dataset.leaked='yes'}catch(e){document.body.dataset.blocked='yes'}</script>`;
        await window.renderShotlist(html);
        return !document.body.dataset.leaked && !document.querySelector("iframe");
    });
    if (!isolated) throw new Error("Sandbox isolation / cleanup failed");
    console.log("Sandbox isolation and cleanup: PASS");
} finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
}
