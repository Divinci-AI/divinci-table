// A dress rehearsal of docs/PLAYTEST-1.md, played end to end by browsers on a local table: Michael is the DM on a
// desktop, Ana and Ben are on phones (2D), Cy is on a desktop (3D) and also has the headset page (/xr) open.
// Three scenes (tavern brawl, road ambush, the goblin cave), with the plan's measurements: room load per device,
// location switch to every device, moves reaching the others, dice (phone and real), damage and conditions owned
// by their players, someone going down, the survey. Prints a report and writes it as JSON for the bug list.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/dnd_playtest_rehearsal.cjs [report.json]
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = 8000 + Math.floor(Math.random() * 900) + 60;
const BASE = `http://127.0.0.1:${PORT}`;
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "dnd-rehearsal-"));
const OUT = process.argv[2];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const state = async () => (await fetch(BASE + "/api/dnd")).json();
const api = async (p, b) => (await fetch(BASE + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) })).json();
const until = async (f, ms = 8000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await f()) return Date.now() - t; await sleep(50); } return null; };

const report = { started: new Date().toISOString(), checks: [], metrics: [], findings: [] };
const check = (ok, what) => { report.checks.push({ ok: !!ok, what }); console.log((ok ? "  ✅ " : "  ❌ ") + what); return ok; };
const metric = (what, device, ms, limit) => {
  const ok = ms !== null && ms <= limit;
  report.metrics.push({ what, device, ms, limit, ok });
  console.log(`  ${ok ? "⏱ " : "⚠️ "} ${what} · ${device}: ${ms === null ? "never" : ms + " ms"} (target ≤ ${limit} ms)`);
};

const SHOWN = `(() => {                                  // what a 3D view shows: the built room, the placeholder, or nothing
  const v = window.DND3D || window.DNDXR; if (!v) return "no-3d";
  const c = v.dnd.root.children[0]?.children[0]; if (!c) return "empty";
  let boxes = 0; c.traverse(o => { if (o.isMesh && o.geometry?.type === "BoxGeometry") boxes++; });
  return boxes ? "placeholder" : "room"; })()`;

function free(st, tok, far = 1) {                          // an open square `far` squares away from a token
  const rows = st.map.layout, taken = new Set(Object.values(st.map.tokens).map(t => t.x + "," + t.y));
  for (const [dx, dy] of [[1, 0], [0, 1], [-1, 0], [0, -1], [1, 1], [-1, 1], [1, -1], [-1, -1]]) {
    const x = tok.x + dx * far, y = tok.y + dy * far, c = (rows[y] || "")[x];
    if (c && ".,=PM".includes(c) && !taken.has(x + "," + y)) return [x, y];
  }
  return null;
}

(async () => {
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Ana,Ben,Cy", "--dm", "Michael", "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", HF_HUB_OFFLINE: "1", TABLE_RESEARCH_DIR: TMP }, stdio: "ignore" });
  const browsers = [];                                   // one browser per device, as in real life: a phone is its own browser
  const errors = [];
  try {
    for (let i = 0; i < 80; i++) { try { await state(); break; } catch { await sleep(250); } }
    const launch = async () => { const b = await chromium.launch({ headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] }); browsers.push(b); return b; };

    // ── setup: everyone opens the room ─────────────────────────────────────────────────────────
    const devices = {};
    const open = async (key, who, kind) => {
      const viewport = kind === "phone" ? { width: 390, height: 844 } : { width: 1280, height: 900 };
      const ctx = await (await launch()).newContext({ viewport, isMobile: kind === "phone" });
      const page = await ctx.newPage();
      page.on("pageerror", e => errors.push(`${key}: ${e.message}`));
      page.on("console", m => {                       // the refusals the rehearsal provokes on purpose are not errors
        if (m.type() === "error" && !/Failed to load resource: the server responded with a status of (403|409)/.test(m.text())) errors.push(`${key}: ${m.text()}`);
      });
      const t0 = Date.now(), loads = {};
      page.on("response", r => { const u = r.url(); if (u.includes("/dnd-assets/")) loads[u.split("/").pop().split("?")[0]] = Date.now() - t0; });
      await page.goto(`${BASE}/${kind === "xr" ? "xr" : ""}?player=${who}`);
      devices[key] = { page, who, kind, t0, loads };
      return devices[key];
    };
    await open("Michael (DM, desktop)", "Michael", "desktop");
    await open("Ana (phone)", "Ana", "phone");
    await open("Ben (phone)", "Ben", "phone");
    await open("Cy (desktop 3D)", "Cy", "desktop");
    await open("Cy (headset page)", "Cy", "xr");
    const as = (d, url, body) => d.page.evaluate(([u, b]) => post(u, b), [url, body]);   // a person acting from their own page
    for (const d of Object.values(devices))
      if (d.kind !== "xr") await d.page.waitForFunction(n => typeof S !== "undefined" && S && S.map && window.TableSeat && TableSeat.name() === n, d.who);
    const dmApi = (url, body) => as(devices["Michael (DM, desktop)"], url, body);       // the DM acts from the DM's page

    for (const [key, d] of Object.entries(devices)) {
      if (d.kind === "phone") {
        const ms = await until(async () => d.loads["map.jpg"] !== undefined, 10000);
        metric("room load (2D map picture)", key, ms === null ? null : d.loads["map.jpg"], 5000);
      } else {
        const ms = await until(async () => (await d.page.evaluate(SHOWN)) === "room", 15000);
        metric(`room load (${d.kind === "xr" ? "headset 3D" : "3D room"})`, key, ms === null ? null : Date.now() - d.t0, 5000);
      }
    }
    for (const who of ["Ana", "Ben", "Cy"]) {
      const d = Object.values(devices).find(x => x.who === who && x.kind !== "xr");
      await as(d, "/api/dnd/sheet", { pregen: { Ana: "fighter", Ben: "rogue", Cy: "wizard" }[who] });
    }
    const st0 = await state();
    check(["Ana", "Ben", "Cy"].every(n => st0.sheets[n]), "every player has a pregen sheet");

    // a location switch from the DM's page (its location picker), timed to every device
    const sw = async (id, label) => {
      const dmPage = devices["Michael (DM, desktop)"].page;
      await dmPage.waitForFunction(n => document.getElementById("loc").options.length >= n, 8, { timeout: 8000 }).catch(() => {});
      const t = Date.now();
      await dmPage.selectOption("#loc", id);
      for (const [key, d] of Object.entries(devices)) {
        const ok = await until(async () => {
          const loc = await d.page.evaluate(() => { const st = window.DNDXR ? DNDXR.state() : (typeof S !== "undefined" ? S : null);   // dnd.html: a top-level let; /xr: inside a module
            return (st && st.map && st.map.location) || null; });
          if (loc !== id) return false;
          return d.kind === "phone" ? true : (await d.page.evaluate(SHOWN)) === "room";
        }, 12000);
        metric(`location switch → ${label}`, key, ok === null ? null : Date.now() - t, 5000);
      }
    };
    // a player's move, timed until every other device has it
    const moveSeen = async (mover, tokId, to) => {
      const t = Date.now();
      const r = await as(mover, "/api/dnd/map/move", { token: tokId, x: to[0], y: to[1] });
      if (r && r.error) return { error: r.error };
      for (const [key, d] of Object.entries(devices)) {
        if (d === mover) continue;
        const ok = await until(async () => d.page.evaluate(([id, x, y]) => { const st = window.DNDXR ? DNDXR.state() : (typeof S !== "undefined" ? S : null); const k = ((st && st.map && st.map.tokens) || {})[id]; return !!k && k.x === x && k.y === y; }, [tokId, ...to]), 6000);
        metric("a move reaches the others", key, ok === null ? null : Date.now() - t, 1000);
      }
      return {};
    };
    const player = who => Object.values(devices).find(x => x.who === who && x.kind !== "xr");
    const fight = async (label, monsters) => {
      await dmApi("/api/dnd/initiative", { action: "start" });
      for (const who of ["Ana", "Ben"]) await as(player(who), "/api/dnd/roll", { dice: "d20", why: "initiative" });
      await as(player("Cy"), "/api/dnd/roll", { dice: "d20", why: "initiative", physical: [14] });   // a real die, entered
      const set = await until(async () => { const i = (await state()).initiative; return i.active && !i.pending.length; }, 8000);
      check(set !== null, `${label}: initiative is set (players rolled; the table rolled ${monsters.join(", ")})`);
      const order = (await state()).initiative.order.map(o => o.name);
      for (let step = 0; step < order.length; step++) {
        const st = await state(), now = st.initiative.order[st.initiative.turn].name;
        const tok = Object.values(st.map.tokens).find(t => t.name === now);
        if (tok && ["Ana", "Ben", "Cy"].includes(now)) {
          const to = free(st, tok);
          if (to) { const r = await moveSeen(player(now), tok.id, to); if (r.error) check(false, `${label}: ${now}'s one-square move was refused: ${r.error}`); }
        } else if (tok) {
          const to = free(st, tok);
          if (to) await dmApi("/api/dnd/map/move", { token: tok.id, x: to[0], y: to[1] });
        }
        await dmApi("/api/dnd/initiative", { action: "next" });
      }
      check((await state()).initiative.round === 2, `${label}: a full round came round`);
      return order;
    };

    // ── scene 1: the tavern, a brawl ─────────────────────────────────────────────────────────
    console.log("\nScene 1 · The Sleeping Griffin tavern");
    await sw("tavern", "tavern");
    await as(player("Ana"), "/api/chat", { text: "Who's buying?" });
    await as(player("Ben"), "/api/dnd/act", { text: "I lean on the bar and listen for rumours." });
    await dmApi("/api/dnd/narrate", { text: 'A stranger offers a job. Two bandits shove over a table!\nTABLE: {"monsters":[{"name":"Bandit 1"},{"name":"Bandit 2"}]}' });
    check(await until(async () => Object.values((await state()).map.tokens).filter(t => /Bandit/.test(t.name)).length === 2) !== null, "tavern: the DM's two bandits are on the map");
    await fight("tavern brawl", ["Bandit 1", "Bandit 2"]);
    const ev1 = (await (await fetch(BASE + "/api/events?since=0")).json()).events;
    check(ev1.some(e => e.type === "roll" && e.by === "Cy" && /14/.test(e.text || "")), "a real-die entry (Cy's 14) is in the public log");
    check(ev1.filter(e => e.type === "roll").length >= 3, `every roll is public (${ev1.filter(e => e.type === "roll").length} rolls in the log)`);
    await dmApi("/api/dnd/initiative", { action: "end" });
    await dmApi("/api/dnd/narrate", { text: 'The bandits flee.\nTABLE: {"monsters":[]}' });

    // ── scene 2: the king's road, an ambush ───────────────────────────────────────────────────
    console.log("\nScene 2 · The king's road");
    await sw("road", "road");
    await dmApi("/api/dnd/narrate", { text: 'Goblins burst from the trees, a wolf behind them!\nTABLE: {"monsters":[{"name":"Goblin 1"},{"name":"Goblin 2"},{"name":"Goblin 3"},{"name":"Goblin 4"},{"name":"Wolf"}]}' });
    check(await until(async () => Object.values((await state()).map.tokens).filter(t => /Goblin|Wolf/.test(t.name)).length === 5) !== null, "road: 4 goblins and a wolf are on the map");
    await dmApi("/api/dnd/initiative", { action: "start" });
    for (const who of ["Ana", "Ben", "Cy"]) await as(player(who), "/api/dnd/roll", { dice: "d20", why: "initiative" });
    await until(async () => { const i = (await state()).initiative; return i.active && !i.pending.length; });
    for (let guard = 0; guard < 12; guard++) {                // to Ben's turn, then try a move far beyond his speed
      const st = await state(), now = st.initiative.order[st.initiative.turn].name;
      if (now === "Ben") break;
      await dmApi("/api/dnd/initiative", { action: "next" });
    }
    const stB = await state(), ben = stB.map.tokens.ben, far = free(stB, ben, 9);
    if (far) {
      const r = await as(player("Ben"), "/api/dnd/map/move", { token: "ben", x: far[0], y: far[1] });
      check(r && r.error && /ft|speed|far|move/i.test(r.error), `road: a 45-ft move on Ben's 30-ft turn is refused ("${(r && r.error) || "allowed!"}")`);
    } else check(false, "road: no open square 9 away from Ben to test the speed limit");
    const cy3d = devices["Cy (desktop 3D)"].page;
    const st2 = await state(), near = free(st2, st2.map.tokens.ben);
    await as(player("Ben"), "/api/dnd/map/move", { token: "ben", x: near[0], y: near[1] });
    const anim = await until(() => cy3d.evaluate(([x, z]) => { const d = DND3D.dnd.debug().ben; return d && Math.abs(d.at[0] - x) < 0.05 && Math.abs(d.at[1] - z) < 0.05; },
                                                    [near[0] + 0.5, near[1] + 0.5]), 6000);
    check(anim !== null, "road: Cy's 3D table walks Ben's token to its new square");
    await dmApi("/api/dnd/initiative", { action: "end" });
    await dmApi("/api/dnd/narrate", { text: 'The ambush breaks.\nTABLE: {"monsters":[]}' });

    // ── scene 3: the goblin cave, the boss ───────────────────────────────────────────────────
    console.log("\nScene 3 · The goblin cave");
    await sw("cave", "cave");
    await dmApi("/api/dnd/narrate", { text: 'A bugbear rises from the dark water, two goblins at its side.\nTABLE: {"monsters":[{"name":"Bugbear"},{"name":"Goblin 1"},{"name":"Goblin 2"}]}' });
    check(await until(async () => Object.values((await state()).map.tokens).filter(t => /Bugbear|Goblin/.test(t.name)).length === 3) !== null, "cave: the bugbear and two goblins are on the map");
    await fight("cave fight", ["Bugbear", "Goblin 1", "Goblin 2"]);
    await as(player("Ben"), "/api/dnd/condition", { condition: "poisoned", on: true });
    const poisoned = await until(() => devices["Ana (phone)"].page.evaluate(() => (S.sheets.Ben?.conditions || []).includes("poisoned")), 4000);
    check(poisoned !== null, "cave: Ben marks himself poisoned and Ana's phone shows it");
    const dmTry = await dmApi("/api/dnd/hp", { delta: -5 });
    check(dmTry && dmTry.error, `cave: the DM can't change Ana's hit points ("${(dmTry && dmTry.error) || "allowed!"}")`);
    await as(player("Ana"), "/api/dnd/hp", { delta: -999 });
    const down = await until(async () => (await state()).sheets.Ana.hp === 0, 4000);
    const downEv = (await (await fetch(BASE + "/api/events?since=0")).json()).events.some(e => /Ana: 0\/\d+ HP — down/.test(e.text || ""));
    check(down !== null && downEv, "cave: Ana takes her own damage, goes down, and the log says so");
    await dmApi("/api/dnd/initiative", { action: "end" });

    // ── after: the survey ─────────────────────────────────────────────────────────────────────
    console.log("\nAfter");
    for (const who of ["Ana", "Ben", "Cy"]) {
      const r = await as(player(who), "/api/survey", { scale: { "I felt content": 3, "I felt satisfied": 3 }, best: "the ambush", worst: "rehearsal" });
      check(r && r.ok, `${who}'s survey is saved`);
    }
    const files = fs.readdirSync(TMP, { recursive: true }).filter(f => path.basename(String(f)).startsWith("survey-human-"));
    check(files.length === 3, `three surveys in the room's research folder, none in the public log (${files.length} files)`);
    const ev = (await (await fetch(BASE + "/api/events?since=0")).json()).events;
    check(!ev.some(e => /the ambush/.test(e.text || "")), "survey answers are not in the public log");
    for (const [key, d] of Object.entries(devices))
      if (d.kind === "phone") check(await d.page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${key}: no horizontal scrolling`);
    const xr = devices["Cy (headset page)"].page;
    const ft = await xr.evaluate(() => { const f = DNDXR.frames.slice(-120); return f.length ? f.reduce((a, b) => a + b, 0) / f.length : null; });
    report.metrics.push({ what: "headset page frame time (headless, software GL: a ceiling, not the Quest)", device: "Cy (headset page)", ms: ft && Math.round(ft), limit: null, ok: true });
    console.log(`  ⏱  headset page frame time (software GL, headless): ${ft && ft.toFixed(1)} ms`);
    check(!errors.length, "no page errors on any device" + (errors.length ? ": " + errors.slice(0, 4).join(" | ") : ""));
  } catch (e) {
    check(false, "the rehearsal crashed: " + e.message);
  } finally {
    for (const b of browsers) await b.close();
    srv.kill();
    fs.rmSync(TMP, { recursive: true, force: true });
  }
  const bad = report.checks.filter(c => !c.ok).length, slow = report.metrics.filter(m => !m.ok).length;
  report.summary = { checks: report.checks.length, failed: bad, metrics: report.metrics.length, over_target: slow };
  if (OUT) fs.writeFileSync(OUT, JSON.stringify(report, null, 1));
  console.log(`\n${report.checks.length - bad}/${report.checks.length} rehearsal checks passed; ${slow}/${report.metrics.length} measurements over target`);
  process.exit(bad ? 1 : 0);
})();
