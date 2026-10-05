// After a deploy: is table.divinci.ai running the commit we meant, and does it show people what it should?
//
//   PW=/path/to/node_modules/@playwright/test node scripts/cloud_smoke.cjs <sha> [--room <id>] [--base https://…]
//
// Checks the Worker's /version, the D&D room files (right types; bad paths refused), and with --room, a fresh
// browser (no cache, no cookies) opening that room: its container's /api/version, and that the 3D table shows
// the built room rather than the placeholder boxes. That last check is the one the test suites didn't make
// when the first cloud D&D room kept the placeholder (2026-10-05). --room uses an existing room on purpose:
// the smoke check never creates rooms, which start containers and show up in the public lobby.
const args = process.argv.slice(2);
const sha = args[0];
const opt = k => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const BASE = opt("--base") || "https://table.divinci.ai";
const room = opt("--room");
const results = [];
const check = (ok, what) => { results.push(!!ok); console.log((ok ? "  ✅ " : "  ❌ ") + what); };

(async () => {
  if (!/^[0-9a-f]{40}$/.test(sha || "")) { console.error("usage: cloud_smoke.cjs <40-char sha> [--room <id>]"); process.exit(2); }
  const v = await fetch(BASE + "/version", { cache: "no-store" }).then(r => r.json()).catch(e => ({ error: e.message }));
  check(v.worker === sha, `the Worker runs ${sha.slice(0, 10)} (it says ${String(v.worker || v.error).slice(0, 10)})`);
  for (const [p, type] of [["locations/tavern/map.jpg", "image/jpeg"], ["locations/crypt/room.glb", "model/gltf-binary"]]) {
    const r = await fetch(`${BASE}/dnd-assets/${p}`);
    check(r.status === 200 && (r.headers.get("content-type") || "").startsWith(type), `/dnd-assets/${p}: ${r.status} ${r.headers.get("content-type")}`);
  }
  for (const p of ["locations/tavern/room.glb.html", "x/minis/goblin.glb", "locations/nowhere/room.glb"]) {
    const r = await fetch(`${BASE}/dnd-assets/${p}`, { redirect: "manual" });
    check(r.status === 404, `/dnd-assets/${p} is refused (${r.status})`);
  }
  if (room) {
    const { chromium } = require(process.env.PW || "@playwright/test");
    const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    try {
      const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
      const page = await ctx.newPage();
      await page.goto(`${BASE}/r/${room}`, { waitUntil: "domcontentloaded" });
      const cv = await page.evaluate(() => fetch("/api/version", { cache: "no-store" }).then(r => r.json())).catch(e => ({ error: e.message }));
      check(cv.sha === sha, `room ${room}'s container runs ${sha.slice(0, 10)} (it says ${String(cv.sha || cv.error).slice(0, 10)})`);
      let shown = "", t0 = Date.now();
      while (Date.now() - t0 < 20000) {
        shown = await page.evaluate(() => {
          if (!window.DND3D) return "no-3d";
          const c = DND3D.dnd.root.children[0]?.children[0];
          if (!c) return "empty";
          let boxes = 0; c.traverse(o => { if (o.isMesh && o.geometry?.type === "BoxGeometry") boxes++; });
          return boxes ? "placeholder" : "room";
        }).catch(e => "error");
        if (shown === "room") break;
        await page.waitForTimeout(400);
      }
      check(shown === "room", `a fresh browser sees the built 3D room (${shown}, ${((Date.now() - t0) / 1000).toFixed(1)} s)`);
    } finally { await browser.close(); }
  }
  const bad = results.filter(x => !x).length;
  console.log(bad ? `\nSMOKE-FAILED ${bad}/${results.length}` : `\nSMOKE-OK ${sha.slice(0, 10)} (${results.length} checks)`);
  process.exit(bad ? 1 : 0);
})();
