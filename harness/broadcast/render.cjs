// Step the broadcast page through its replay, one screenshot per frame: node render.cjs URL OUTDIR FPS [MAX_SECONDS]
// Deterministic: it calls window.showAt(t) for t = i/FPS, so the video does not depend on how fast this machine is.
const fs = require("fs");
const path = require("path");
const { chromium } = require(process.env.PW || "playwright");
(async () => {
  const [url, outdir, fpsArg, maxArg] = process.argv.slice(2);
  const fps = Number(fpsArg || 2), maxSecs = Number(maxArg || 1e9);
  fs.mkdirSync(outdir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const requests = [];
  page.on("request", (r) => requests.push(new URL(r.url()).pathname));
  await page.goto(url);
  await page.waitForFunction("window.ready === true", null, { timeout: 15000 });
  const duration = Math.min(await page.evaluate("window.duration"), maxSecs);
  const n = Math.ceil(duration * fps);
  for (let i = 0; i < n; i++) {
    await page.evaluate((t) => window.showAt(t), i / fps);
    await page.screenshot({ path: path.join(outdir, `frame_${String(i).padStart(5, "0")}.png`) });
  }
  await browser.close();
  console.log(JSON.stringify({ frames: n, duration, requests: [...new Set(requests)] }));
})().catch((e) => { console.error(String(e).slice(0, 400)); process.exit(1); });
