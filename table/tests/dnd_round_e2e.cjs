// A full combat round played from two phones (docs/DND-3D-GOAL.md D5): Michael and Sam at phone width, Dana as
// the DM (through the API). Roll initiative from a phone, both roll d20 from the dice panel, each person moves
// their own token on their turn by tapping the 2D map and passes with Next turn, the DM moves the goblin on its
// turn, and the round comes round to 2. Then Michael holds 🎙 while a fake microphone plays a spoken action, and
// the action reaches the story log (Whisper on this Mac). macOS only (`say` makes the spoken line).
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/dnd_round_e2e.cjs
const path = require("path");
const fs = require("fs");
const { spawn, execFileSync } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = 8000 + Math.floor(Math.random() * 900) + 50;
const BASE = `http://127.0.0.1:${PORT}`;
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "dnd-round-"));
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const state = async () => (await fetch(BASE + "/api/dnd")).json();
const api = async (p, b) => (await fetch(BASE + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) })).json();
const until = async (f, ms = 6000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await f()) return true; await sleep(150); } return false; };

function freeNeighbour(st, tok) {                         // an open square next to a token (in its speed, by the rules)
  const rows = st.map.layout, taken = new Set(Object.values(st.map.tokens).map(t => t.x + "," + t.y));
  for (const [dx, dy] of [[1, 0], [0, 1], [-1, 0], [0, -1], [1, 1], [-1, 1], [1, -1], [-1, -1]]) {
    const x = tok.x + dx, y = tok.y + dy, c = (rows[y] || "")[x];
    if (c && ".,=~DPM".includes(c) && !taken.has(x + "," + y)) return [x, y];
  }
  return null;
}

(async () => {
  execFileSync("say", ["-o", path.join(TMP, "act.aiff"), "I draw my longsword and charge the goblin by the barrels."]);
  execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-i", path.join(TMP, "act.aiff"), "-ar", "48000", "-ac", "1", path.join(TMP, "act.wav")]);
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Michael,Sam", "--dm", "Dana", "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", HF_HUB_OFFLINE: "1", TABLE_RESEARCH_DIR: TMP }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 60; i++) { try { await state(); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true, args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${path.join(TMP, "act.wav")}%noloop`] });
    const errors = [];
    const phone = async who => {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: false, permissions: ["microphone"] });
      const page = await ctx.newPage();
      page.on("pageerror", e => errors.push(`${who}: ${e.message}`));
      await page.goto(`${BASE}/?player=${who}`);
      await page.waitForFunction(n => typeof S !== "undefined" && S && S.map && window.TableSeat && TableSeat.name() === n, who);
      return page;
    };
    const pages = { Michael: await phone("Michael"), Sam: await phone("Sam") };
    const dana = await api("/api/seat/claim", { name: "Dana" });
    const dm = b => ({ by: "Dana", key: dana.key, ...b });
    await api("/api/dnd/narrate", dm({ text: 'A goblin leaps out!\nTABLE: {"monsters":[{"name":"Goblin 1"}]}' }));
    check(await until(async () => !!(await state()).map.tokens["goblin-1"]), "the DM's goblin is on the map");

    await pages.Michael.click("#iniStart");
    check(await until(async () => (await state()).initiative.active), "a phone starts initiative");
    for (const who of ["Michael", "Sam"]) {
      await pages[who].fill("#why", "initiative");
      await pages[who].click('#dice button[data-d="d20"]');
    }
    check(await until(async () => { const i = (await state()).initiative; return i.active && !i.pending.length && i.order.length === 3; }),
          "both players rolled from their phones; the goblin was rolled by the table");

    const order = (await state()).initiative.order.map(o => o.name);
    let movedBy = [];
    for (let step = 0; step < order.length; step++) {
      const st = await state(), now = st.initiative.order[st.initiative.turn].name;
      if (now === "Goblin 1") {
        const g = st.map.tokens["goblin-1"], to = freeNeighbour(st, g);
        const gm = await api("/api/dnd/map/move", dm({ token: "goblin-1", x: to[0], y: to[1] }));
        if (gm.error) console.log("    goblin move:", gm.error);
        await api("/api/dnd/initiative", dm({ action: "next" }));
        movedBy.push(now); continue;
      }
      const page = pages[now], tok = st.map.tokens[now.toLowerCase()], to = freeNeighbour(st, tok);
      await page.locator("#map").scrollIntoViewIfNeeded();    // the dice and Next turn are further down the page
      const box = await page.locator("#map").boundingBox(), cell = await page.evaluate(() => MAP.cell);
      await page.mouse.click(box.x + (tok.x + .5) * cell, box.y + (tok.y + .5) * cell);
      await page.mouse.click(box.x + (to[0] + .5) * cell, box.y + (to[1] + .5) * cell);
      const ok = await until(async () => { const t = (await state()).map.tokens[now.toLowerCase()]; return t.x === to[0] && t.y === to[1]; });
      if (ok) movedBy.push(now);
      else console.log(`    ${now} at ${tok.x},${tok.y} → ${to}: still at`, JSON.stringify((await state()).map.tokens[now.toLowerCase()]),
                       "err:", await page.evaluate(() => document.getElementById("err").textContent), "hint:", await page.evaluate(() => document.getElementById("maphint").textContent));
      await page.click("#iniNext");
      await until(async () => (await state()).initiative.order[(await state()).initiative.turn].name !== now);
    }
    const st = await state();
    check(movedBy.length === 3, `everyone moved on their own turn, in order (${order.join(" → ")})`);
    check(st.initiative.round === 2 && st.initiative.turn === 0, `the round came round (round ${st.initiative.round}, turn ${st.initiative.turn})`);

    const mike = pages.Michael;
    await mike.locator("#talk").scrollIntoViewIfNeeded();
    const tb = await mike.locator("#talk").boundingBox();
    await mike.mouse.move(tb.x + tb.width / 2, tb.y + tb.height / 2);
    await mike.mouse.down(); await sleep(5200); await mike.mouse.up();
    const heard = await until(async () => {
      const ev = (await (await fetch(BASE + "/api/events?since=0")).json()).events;
      return ev.some(e => e.type === "say" && e.by === "Michael" && /longsword/i.test(e.text || ""));
    }, 20000);
    check(heard, "holding 🎙 on the phone: the spoken action reaches the story log as Michael's");
    for (const [who, page] of Object.entries(pages))
      check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${who}'s phone: no horizontal scrolling`);
    check(!errors.length, "no page errors" + (errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""));
  } finally {
    if (browser) await browser.close();
    srv.kill();
    fs.rmSync(TMP, { recursive: true, force: true });
  }
  const ok = results.filter(r => r[0]).length;
  console.log(`\n${ok}/${results.length} two-phone round checks passed`);
  process.exit(ok === results.length ? 0 : 1);
})();
