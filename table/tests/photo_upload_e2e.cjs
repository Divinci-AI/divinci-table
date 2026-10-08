// Photo upload (assets/photo.js) in a real browser, with the network stubbed. Includes Brave's behaviour on an iPhone, which
// blanks canvas readback: a photo that fits is sent untouched, and a shrink that comes back black is refused, never sent.
//   PW=/path/to/@playwright/test node table/tests/photo_upload_e2e.cjs
const { chromium } = require(process.env.PW || "@playwright/test");
const fs = require("fs"), path = require("path");
let failed = [];
const check = (n, ok, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + d}`); if (!ok) failed.push(n); };
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const sent = [];
  await ctx.route("http://photo.test/**", async r => {
    const u = r.request().url();
    if (u.endsWith("/api/chat/photo")) { sent.push(r.request().postDataBuffer()); return r.fulfill({ json: { ok: true, photo: "/photos/x.jpg" } }); }
    return r.fulfill({ contentType: "text/html", body: "<!doctype html><title>t</title><body>x</body>" });
  });
  const page = await ctx.newPage();
  await page.goto("http://photo.test/");
  await page.addScriptTag({ path: path.resolve(__dirname, "../assets/photo.js") });
  // a real JPEG made in the page, small and large; the large one is noise so it stays big
  const make = (w, h, noise, q) => page.evaluate(async ([w, h, noise, q]) => {
    const c = document.createElement("canvas"); c.width = w; c.height = h; const x = c.getContext("2d");
    const im = x.createImageData(w, h);
    for (let i = 0; i < im.data.length; i += 4) { const v = noise ? Math.random() * 255 : (i / 4 % w) / w * 255; im.data[i] = v; im.data[i + 1] = noise ? Math.random() * 255 : 120; im.data[i + 2] = noise ? Math.random() * 255 : 200; im.data[i + 3] = 255; }
    x.putImageData(im, 0, 0);
    const blob = await new Promise(ok => c.toBlob(ok, "image/jpeg", q));
    window.__file = new File([blob], "p.jpg", { type: "image/jpeg" }); (window.__files = window.__files || {})[noise ? "big" : "small"] = window.__file; return blob.size;
  }, [w, h, noise, q]);
  const send = () => page.evaluate(async () => JSON.stringify(await window.sendTablePhoto(window.__file, "Sam", "a photo")));

  const big = await make(2600, 2600, true, 1.0);                      // made first, while the canvas still works
  console.log("a photo that fits");
  const small = await make(800, 600, false, 0.9);
  const r1 = JSON.parse(await send());
  check("it is sent exactly as the camera made it (no canvas)", r1.ok && sent.at(-1).length === small, `${sent.at(-1)?.length} vs ${small}`);

  console.log("Brave blanks the canvas");
  await page.evaluate(() => { const o = HTMLCanvasElement.prototype.toBlob; HTMLCanvasElement.prototype.__orig = o;
    HTMLCanvasElement.prototype.toBlob = function (cb, t, q) { const c = document.createElement("canvas"); c.width = this.width; c.height = this.height;
      const x = c.getContext("2d"); x.fillStyle = "#000"; x.fillRect(0, 0, c.width, c.height); o.call(c, cb, t, q); }; });
  const r2 = JSON.parse(await send());
  check("a small photo still goes through untouched, so Brave's blanking cannot spoil it", r2.ok && sent.at(-1).length === small);
  await page.evaluate(() => { window.__file = window.__files.big; });
  const n = sent.length;
  const r3 = JSON.parse(await send());
  check("a photo too big to send, shrunk to black by the browser, is refused with the reason, not sent",
        big > 7_500_000 && r3.error && /blanked|Shields/i.test(r3.error) && sent.length === n, `${big} ${JSON.stringify(r3)} ${sent.length}/${n}`);

  console.log("a normal browser shrinks a big photo properly");
  await page.evaluate(() => { HTMLCanvasElement.prototype.toBlob = HTMLCanvasElement.prototype.__orig; });
  const r4 = JSON.parse(await send());
  const up = sent.at(-1);
  const bright = await page.evaluate(async b => { const im = await createImageBitmap(new Blob([new Uint8Array(b)], { type: "image/jpeg" }));
    const c = document.createElement("canvas"); c.width = 8; c.height = 8; const x = c.getContext("2d"); x.drawImage(im, 0, 0, 8, 8);
    const d = x.getImageData(0, 0, 8, 8).data; let s = 0; for (let i = 0; i < d.length; i += 4) s += d[i] + d[i + 1] + d[i + 2]; return s / 64 / 3; }, [...up]);
  check("it is shrunk under the limit and is not black", r4.ok && up.length < 7_500_000 && up.length < big && bright > 20, `${up.length} bright ${bright}`);
  await browser.close();
  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join("; ")}` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
