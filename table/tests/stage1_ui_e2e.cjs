// Stage 1 of docs/hud-plan.md, the parts that live in the pages: a real browser (Chromium, one context) against a real local
// server on its own port (8832) and research dir, as a REMOTE player (every request, the page's and this script's, carries
// X-Forwarded-For, which is what the cloud Worker adds, so the server's seat and pilot-key rules apply).
//   PW=/path/to/@playwright/test node table/tests/stage1_ui_e2e.cjs
// Item 1  /hand: Begin is only enabled when the server would accept it.
// Item 4  /hand and /board: no Untap button unless it is legal; a refusal's reason is shown.
// Item 5  /hand's priority alert and the red Pass bar really pass for a pilot (POST /api/brain/pass, not /api/phase/next).
// Item 6  /me never presses NEXT by itself when its checklist request fails.
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path");
const REPO = path.resolve(__dirname, "../..");
const PORT = 8832, BASE = `http://127.0.0.1:${PORT}`;
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
const XFF = { "X-Forwarded-For": "203.0.113.9" };
let failed = [];
const check = (name, ok, detail = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${name}${ok ? "" : " — " + String(detail).slice(0, 300)}`); if (!ok) failed.push(name); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let TOKEN = "";
const j = async (m, p, b, h = {}) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", "User-Agent": "stage1-node", ...XFF, ...h }, body: b ? JSON.stringify(b) : undefined }); return [r.status, await r.json().catch(() => ({}))]; };
const host = async (p, b) => { const r = await fetch(BASE + p, { method: "POST", headers: { "Content-Type": "application/json", "X-Brain-Token": TOKEN }, body: JSON.stringify(b) }); return [r.status, await r.json().catch(() => ({}))]; };
const phase = async () => (await j("GET", "/api/phase"))[1];

async function withServer(order, fn) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "stage1ui-"));
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token",
    "--ai", "Claude|Kaust, Eyes of the Glade|", "--ai-deck", "decks/kaust.json", "--pilot", "Claude", "--human", "Michael|", "--human", "Sam|",
    "--order", order, "--priority-window", "0.2", "--fair-seed", "off"],
    { cwd: REPO, env: { ...process.env, TABLE_CLOUD: "1", HF_HUB_OFFLINE: "1", ROUTER: "code", REPLIES: "template", TABLE_RESEARCH_DIR: tmp + "/research", TYPESAFE_API_KEY: "" }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 180; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    TOKEN = fs.readFileSync(tmp + "/token", "utf8").trim();
    const keys = {};
    for (const n of ["Claude", "Michael", "Sam"]) keys[n] = (await j("POST", "/api/seat/claim", { name: n }, undefined, { "User-Agent": "k-" + n }))[1].key;
    browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: { width: 390, height: 780 }, extraHTTPHeaders: XFF });
    await fn({ ctx, keys });
  } catch (e) { check("the run completed", false, e && e.stack || e); }
  finally { if (browser) await browser.close(); srv.kill(); await sleep(500); }
}

const open = async (ctx, url, name) => {
  const page = await ctx.newPage();
  await page.goto(BASE + url);
  await page.waitForFunction(n => window.TableSeat && TableSeat.name() === n, name, { timeout: 15000 });
  return page;
};
const nextFor = async (who, keys) => (await j("POST", "/api/phase/next", { by: who, key: keys[who] }))[0];
const humansPass = async keys => {                     // whichever person is next in the round passes
  const p = await phase(), nx = p.passes && p.passes.next;
  if (nx === "Michael" || nx === "Sam") await nextFor(nx, keys);
  return p;
};

(async () => {
  // ───────── server A: Claude (a pilot) goes first, then Michael and Sam ─────────
  await withServer("Claude,Michael,Sam", async ({ ctx, keys }) => {
    const claudeAuth = { "X-Seat-Key": keys.Claude };
    const hand = await open(ctx, `/hand?player=Claude&key=${encodeURIComponent(keys.Claude)}`, "Claude");

    console.log("item 1: Begin only when legal");
    await sleep(2500);
    check("before the game starts, Begin is disabled", await hand.evaluate(() => document.getElementById("begin").disabled));
    await j("POST", "/api/phase/next", { by: "Claude", key: keys.Claude });                  // the pilot starts the game: Claude's turn
    await hand.waitForFunction(() => !document.getElementById("begin").disabled, null, { timeout: 8000 }).catch(() => {});
    check("on Claude's own turn, at the untap step, Begin is enabled", await hand.evaluate(() => !document.getElementById("begin").disabled));
    const noBegin = await hand.evaluate(() => [
      (PH = { player: "Michael", begun: false, index: 0, steps: ["untap", "upkeep", "draw", "main 1"] }, canBegin()),
      (PH = { player: "Claude", begun: true, index: 3, steps: ["untap", "upkeep", "draw", "main 1"] }, canBegin()),
      (PH = { player: "Claude", begun: false, index: 3, steps: ["untap", "upkeep", "draw", "main 1"] }, canBegin()),
      (PH = { player: "Claude", begun: false, index: 0, steps: ["untap", "upkeep", "draw", "main 1"] }, canBegin())]);
    check("the page's rule: not on someone else's turn, not after begun, not past main 1; yes at untap", JSON.stringify(noBegin) === "[false,false,false,true]", JSON.stringify(noBegin));
    await hand.evaluate(() => refreshPhase());

    console.log("item 6: /me does not press NEXT when its checklist request fails");
    const meCtx = await ctx.browser().newContext({ viewport: { width: 390, height: 780 }, extraHTTPHeaders: XFF });
    const me = await meCtx.newPage();
    let nexts = 0, checklists = 0;
    me.on("request", r => { if (r.method() === "POST" && /\/api\/phase\/next$/.test(r.url())) nexts++; });
    await me.route("**/api/checklist*", route => { checklists++; route.fulfill({ status: 502, contentType: "text/html", body: "<html>Bad gateway</html>" }); });
    await me.goto(`${BASE}/me?player=Michael&key=${encodeURIComponent(keys.Michael)}`);
    await me.waitForFunction(() => window.TableSeat && TableSeat.name() === "Michael", null, { timeout: 15000 });
    await sleep(4500);                                                                         // several phase polls, a new step each time the key changes
    check("(the checklist request was made and failed)", checklists >= 1, checklists);
    check("…and /me did not send a NEXT of its own", nexts === 0, `${nexts} POST /api/phase/next`);
    await meCtx.close();

    console.log("item 4: Untap on /hand");
    const land = (await host("/api/brain/search", { seat: "Claude", name: "Forest", to: "hand" }));
    const st0 = (await j("GET", "/api/brain/state?seat=Claude", undefined, claudeAuth))[1];
    const lname = (st0.hand.find(h => h.land) || {}).name;
    await j("POST", "/api/brain/land", { seat: "Claude", name: lname }, claudeAuth);
    const perm = () => j("GET", "/api/brain/state?seat=Claude", undefined, claudeAuth).then(([, s]) => (s.permanents || []).find(p => p.name === lname));
    const lid = (await perm()).id;
    await j("POST", "/api/brain/tap", { seat: "Claude", ref: "#" + lid }, claudeAuth);
    await hand.evaluate(() => load());
    await hand.waitForSelector(`[data-tap="${lid}"]`, { timeout: 5000 }).catch(() => {});
    const t1 = await hand.evaluate(id => (document.querySelector(`[data-tap="${id}"]`) || {}).textContent, lid);
    check("at Claude's untap step a tapped land offers Untap", t1 === "Untap", t1);
    await hand.click(`[data-tap="${lid}"]`);
    await sleep(800);
    check("…and pressing it untaps it", !(await perm()).tapped);
    await hand.evaluate(() => load()); await sleep(300);
    await hand.click(`[data-tap="${lid}"]`);                                                   // Tap, from the page
    await sleep(800);
    check("Tap works from the page", (await perm()).tapped);

    console.log("item 1 (cont.): Begin walks the turn; item 4: no Untap past the untap step");
    await hand.click("#begin");
    for (let i = 0; i < 40; i++) {                                                             // the people pass upkeep and draw, in turn order
      await humansPass(keys);
      const p = await phase();
      if (p.begun) break;
      await sleep(400);
    }
    await hand.waitForFunction(() => PH && PH.begun, null, { timeout: 20000 }).catch(() => {});
    await sleep(500);
    check("begin untapped the land (the start of the turn still untaps everything)", !(await perm()).tapped);
    check("once begun, Begin is disabled again", await hand.evaluate(() => document.getElementById("begin").disabled));
    await hand.click(`[data-tap="${lid}"]`);                                                   // Tap
    await sleep(800);
    await hand.evaluate(() => load()); await sleep(300);
    check("the land is tapped", (await perm()).tapped);
    const untapBtn = await hand.evaluate(id => (document.querySelector(`[data-tap="${id}"]`) || {}).textContent || "(no button)", lid);
    check("in main 1 a tapped land offers NO Untap button", untapBtn === "(no button)", untapBtn);
    await hand.evaluate(id => act("untap", { ref: "#" + id }), lid);
    const err = await hand.evaluate(() => document.getElementById("err").textContent);
    check("if the request is sent anyway the server's reason is shown", /untap step/.test(err), err);
    check("…and the land stays tapped", (await perm()).tapped);

    console.log("item 5: a pilot passes from the alert and from the red bar");
    for (const n of ["Michael", "Sam"]) await j("POST", "/api/autopass", { by: n, key: keys[n], mode: "others" });
    await hand.evaluate(() => { window.__posts = []; const f = window.fetch; window.fetch = (u, i = {}) => { if ((i.method || "GET") === "POST") window.__posts.push(String(u)); return f(u, i); }; });
    let ended = false;
    (async () => { for (let i = 0; i < 60 && !ended; i++) { const [c] = await j("POST", "/api/brain/end", { seat: "Claude", text: "done" }, claudeAuth); if (c === 200) ended = true; else await sleep(1000); } })();
    await hand.waitForFunction(() => PH && PH.player === "Michael", null, { timeout: 60000 }).catch(() => {});
    check("(Claude ended its turn: it is Michael's)", (await phase()).player === "Michael", JSON.stringify(await phase()));
    await nextFor("Michael", keys);                                                            // untap -> upkeep
    await hand.waitForSelector("[data-pass]", { timeout: 15000 }).catch(() => {});
    for (let i = 0; i < 30 && (await phase()).passes.next !== "Michael"; i++) await sleep(300);
    await nextFor("Michael", keys);                                                            // Michael passes upkeep first; Sam is auto-passed; Claude is next
    for (let i = 0; i < 30 && (await phase()).passes.next !== "Claude"; i++) await sleep(300);
    const before = await phase();
    check("(Claude is next to pass at Michael's upkeep)", before.passes.next === "Claude" && before.step === "upkeep", JSON.stringify(before.passes));
    const alertShown = await hand.evaluate(() => !!document.querySelector("[data-pass]"));
    check("the priority alert on /hand has a Pass button", alertShown);
    await hand.evaluate(() => { window.__posts.length = 0; });
    if (alertShown) await hand.click("[data-pass]");
    await sleep(1500);
    const posts = await hand.evaluate(() => window.__posts.slice());
    const after = await phase();
    check("pressing it calls the real pass (POST /api/brain/pass), not only closing the alert", posts.some(u => /\/api\/brain\/pass$/.test(u)), JSON.stringify(posts));
    check("…and the round counted it: Claude passed (the step is finished or ends with the next NEXT)",
      after.passes.passed.includes("Claude") || after.step !== "upkeep", JSON.stringify([after.step, after.passes]));

    // the next window: the finished upkeep ends (the active person's NEXT, unless the window already closed it), Michael passes draw, then the red bar for Claude
    let ph = await phase();
    if (ph.step === "upkeep") await nextFor("Michael", keys);
    for (let i = 0; i < 30; i++) { ph = await phase(); if (ph.step === "draw" && ph.passes.next === "Michael") break; await sleep(300); }
    await nextFor("Michael", keys);
    await hand.waitForFunction(() => document.body.classList.contains("u-on"), null, { timeout: 15000 }).catch(() => {});
    const bar = await phase();
    check("the red Pass bar is up for the pilot", await hand.evaluate(() => document.body.classList.contains("u-on")) && bar.step === "draw", JSON.stringify([bar.step, bar.passes]));
    await hand.evaluate(() => { window.__posts.length = 0; });
    const stepBefore = bar.step;
    await hand.click(".u-pass");
    await sleep(1500);
    const posts2 = await hand.evaluate(() => window.__posts.slice());
    check("its button calls the pilot pass, not /api/phase/next (which refuses a pilot)", posts2.some(u => /\/api\/brain\/pass$/.test(u)) && !posts2.some(u => /\/api\/phase\/next$/.test(u)), JSON.stringify(posts2));
    const fin = await phase();
    check("…and the round counted it", fin.passes.passed.includes("Claude") || fin.step !== stepBefore, JSON.stringify([fin.step, fin.passes]));
  });

  // ───────── server B: Michael (a person with a real deck) goes first ─────────
  await withServer("Michael,Claude,Sam", async ({ ctx, keys }) => {
    console.log("item 4: Untap on /board");
    const mk = { "X-Seat-Key": keys.Michael };
    await j("POST", "/api/my-board", { by: "Michael", key: keys.Michael, permanents: [{ name: "Forest", tapped: true }, { name: "Island" }] }, mk);
    const board = await open(ctx, `/board?player=Michael&key=${encodeURIComponent(keys.Michael)}`, "Michael");
    await board.waitForSelector(".seat .card", { timeout: 10000 });
    const menuHas = async label => {
      await board.evaluate(() => { last = ""; return refresh(); });
      await sleep(400);
      const cards = await board.$$(".seat .card");
      for (const c of cards) { if (/Forest/.test(await c.innerText())) { await c.click(); break; } }
      await sleep(300);
      return board.evaluate(l => [...document.querySelectorAll("#menu button[data-act]")].map(b => b.textContent.trim()), label);
    };
    let btns = await menuHas();
    check("before the game starts a tapped card's menu offers no Untap", !btns.includes("Untap") && btns.includes("Tap") === false, JSON.stringify(btns));
    await j("POST", "/api/phase/next", { by: "Claude", key: keys.Claude });                  // the game starts: Michael, at his untap step
    btns = await menuHas();
    check("at Michael's untap step the menu offers Untap", btns.includes("Untap"), JSON.stringify(btns));
    await board.click('#menu [data-act="untap"]');
    await sleep(800);
    const b3 = async () => (await j("GET", "/api/board3d"))[1].seats.find(s => s.name === "Michael").permanents.map(p => !!p.tapped);
    check("…and pressing it untaps the card", (await b3())[0] === false, JSON.stringify(await b3()));
    await j("POST", "/api/card-action", { seat: "Michael", key: keys.Michael, index: 0, name: "Forest", action: "tap" }, mk);
    await nextFor("Michael", keys);                                                            // the untap step is done: upkeep
    btns = await menuHas();
    check("after the untap step the tapped card's menu offers no Untap", !btns.includes("Untap"), JSON.stringify(btns));
    const [c, d] = await j("POST", "/api/card-action", { seat: "Michael", key: keys.Michael, index: 0, name: "Forest", action: "untap" }, mk);
    check("…and the server refuses it with a reason if sent anyway", c === 409 && /untap step/.test(d.error || ""), JSON.stringify([c, d]));
    await board.evaluate(async () => { const r = await fetch("/api/card-action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seat: "Michael", index: 0, name: "Forest", action: "untap" }) }); window.__why = (await r.json()).error; });
    check("(the page sees the same reason)", /untap step/.test(await board.evaluate(() => window.__why)));
  });

  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join("; ")}` : "\nALL PASS");
  process.exit(failed.length ? 1 : 0);
})();
