// After a deploy: is table.divinci.ai running the commit we meant, and do its rooms answer? (docs/deploy.md)
//
//   node scripts/cloud_smoke.cjs <sha> [--room <id>] [--room-sha <sha>] [--base https://…]
//   PW=/path/to/node_modules/@playwright/test node scripts/cloud_smoke.cjs <sha> --room <id> --browser
//
// Always: the Worker's /version, whether the lobby is in host mode (GET /agents/tables says "hosting"), and the D&D
// room files (right types; bad paths refused). In host mode those files come from the room host through its private
// tunnel, so a 200 there is already a round trip to the room host.
//
// --room <id>: an EXISTING room (the smoke check never creates rooms: they start processes and show up in the public
// lobby). A plain round trip to that room's table: /r/<id> sets the room cookie, then /api/stage must answer with the
// table's seats. Then which commit the room runs: container rooms say so at /api/version; a room host's table answers
// that page only on its own machine (403 to visitors), so in host mode pass --room-sha with the image tag you expect
// and compare it yourself with `docker ps` on the room host (docs/deploy.md), or leave it out.
// --browser (needs Playwright): a fresh browser (no cache, no cookies) opens the room and, for a D&D room, checks the 3D
// table shows the built room rather than the placeholder boxes (the check the suites missed on 2026-10-05).
const args = process.argv.slice(2);
const sha = args[0];
const opt = k => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const BASE = (opt("--base") || "https://table.divinci.ai").replace(/\/+$/, "");
const room = opt("--room");
const roomSha = opt("--room-sha");
const browserCheck = args.includes("--browser");
const UA = { "User-Agent": "divinci-table-smoke/1.0 (+https://github.com/Divinci-AI/divinci-table)" };   // the edge refuses some default agents
const results = [];
const check = (ok, what) => { results.push(!!ok); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const note = what => console.log("  ·  " + what);
const getJson = (path, headers = {}) => fetch(BASE + path, { cache: "no-store", headers: { ...UA, ...headers }, redirect: "manual" })
  .then(async r => ({ status: r.status, body: await r.json().catch(() => null) })).catch(e => ({ status: 0, body: null, error: e.message }));

(async () => {
  if (!/^[0-9a-f]{40}$/.test(sha || "")) { console.error("usage: cloud_smoke.cjs <40-char sha> [--room <id>] [--room-sha <sha>] [--browser]"); process.exit(2); }
  const v = await getJson("/version");
  check(v.body?.worker === sha, `the Worker runs ${sha.slice(0, 10)} (it says ${String(v.body?.worker || v.error || v.status).slice(0, 10)})`);
  const t = await getJson("/agents/tables");
  const host = t.body?.hosting === true;
  check(t.status === 200 && Array.isArray(t.body?.tables), `the lobby answers /agents/tables (${t.status}; ${host ? "host mode" : "container mode"})`);
  for (const [p, type] of [["locations/tavern/map.jpg", "image/jpeg"], ["locations/crypt/room.glb", "model/gltf-binary"]]) {
    const r = await fetch(`${BASE}/dnd-assets/${p}`, { headers: UA });
    await r.arrayBuffer().catch(() => null);
    check(r.status === 200 && (r.headers.get("content-type") || "").startsWith(type),
      `/dnd-assets/${p}: ${r.status} ${r.headers.get("content-type")}${host ? " (from the room host)" : ""}`);
  }
  for (const p of ["locations/tavern/room.glb.html", "x/minis/goblin.glb", "locations/nowhere/room.glb"]) {
    const r = await fetch(`${BASE}/dnd-assets/${p}`, { redirect: "manual", headers: UA });
    await r.arrayBuffer().catch(() => null);
    check(r.status === 404, `/dnd-assets/${p} is refused (${r.status})`);
  }
  if (room) {
    if (!/^[a-z0-9]{8}$/.test(room)) { console.error("--room: an 8-character room id from the lobby"); process.exit(2); }
    const j = await fetch(`${BASE}/r/${room}`, { redirect: "manual", headers: UA });
    await j.arrayBuffer().catch(() => null);
    const cookie = (j.headers.get("set-cookie") || "").split(";")[0];
    check(j.status === 302 && cookie === `room=${room}`, `/r/${room} hands out the room cookie (${j.status})`);
    const st = await getJson("/api/stage", { Cookie: cookie });
    const seats = st.body && (st.body.seats || st.body.players);
    check(st.status === 200 && st.body && typeof st.body === "object",
      `room ${room} answers /api/stage (${st.status}${Array.isArray(seats) ? `, ${seats.length} seats` : ""})`);
    const cv = await getJson("/api/version", { Cookie: cookie });
    if (cv.status === 200) {
      const want = roomSha || sha;
      check(String(cv.body?.sha || "").startsWith(want.slice(0, 10)), `room ${room} runs ${want.slice(0, 10)} (it says ${String(cv.body?.sha).slice(0, 10)})`);
    } else if (roomSha) {
      note(`room ${room} doesn't show its commit to visitors (${cv.status}, as a room host's table should not): ` +
        `compare ${roomSha.slice(0, 10)} with the image tag in \`docker ps\` on the room host`);
    } else {
      note(`room ${room}'s commit isn't shown to visitors (${cv.status}); in host mode read it from \`docker ps\` on the room host`);
    }
  }
  if (room && browserCheck) {
    const { chromium } = require(process.env.PW || "@playwright/test");
    const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    try {
      const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
      const page = await ctx.newPage();
      await page.goto(`${BASE}/r/${room}`, { waitUntil: "domcontentloaded" });
      let shown = "", t0 = Date.now();
      while (Date.now() - t0 < 20000) {
        shown = await page.evaluate(() => {
          if (!window.DND3D) return "no-3d";
          const c = DND3D.dnd.root.children[0]?.children[0];
          if (!c) return "empty";
          let boxes = 0; c.traverse(o => { if (o.isMesh && o.geometry?.type === "BoxGeometry") boxes++; });
          return boxes ? "placeholder" : "room";
        }).catch(() => "error");
        if (shown === "room") break;
        await page.waitForTimeout(400);
      }
      if (shown === "no-3d") note(`room ${room} has no D&D 3D table (not a D&D room): 3D check skipped`);
      else check(shown === "room", `a fresh browser sees the built 3D room (${shown}, ${((Date.now() - t0) / 1000).toFixed(1)} s)`);
    } finally { await browser.close(); }
  }
  const bad = results.filter(x => !x).length;
  console.log(bad ? `\nSMOKE-FAILED ${bad}/${results.length}` : `\nSMOKE-OK ${sha.slice(0, 10)} (${results.length} checks)`);
  process.exit(bad ? 1 : 0);
})();
