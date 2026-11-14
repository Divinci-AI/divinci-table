// 2026-11-13, item 8, in a real browser: a land that does more than make mana opens a menu instead of tapping; a mis-tap is taken back; a
// refused untap says why. A real server on a free port, temp folders, no card art.
//   laptop 1512x860: the mat, real mouse presses.   phone 390x844: the Battlefield list, real touches (CDP).
// Each press is checked by the request the page sent (and did NOT send), the table's state afterwards, and what the page said.
// Not here: a real phone, Safari / WebKit, Firefox; the "pay it, do the rest by hand" button (no card of this set needs it for these lands:
// it is tested on the engine in retro_1113_land_ability_test.py).
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/retro_1113_land_e2e.cjs
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), net = require("net");
const REPO = path.resolve(__dirname, "../..");
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
const CARDS = process.env.TABLE_CARDS_JSON || [path.join(REPO, "table/.cache/cards-trk.json"), path.join(os.homedir(), ".divinci/star-trek-mtg/cards.json")].find(f => fs.existsSync(f));
if (!CARDS) { console.error("SKIP: needs the Star Trek set's card data (TABLE_CARDS_JSON=<file>)"); process.exit(77); }
let failed = [];
const check = (ok, n, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(typeof d === "string" ? d : JSON.stringify(d)).slice(0, 500)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const LIVE = [8800, 8801, 8820, 8830, 8883, 8891];
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(LIVE.includes(p) ? freePort() : p)); }); });
(async () => {
  const PORT = await freePort(), BASE = `http://127.0.0.1:${PORT}`;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "retro1113ld-"));
  const deckScript = `import json,os,sys
sys.path.insert(0,'table')
import make_deck_json_shim as s
C={c['name']:c for c in s.load(sys.argv[2])}
P={'Swamp':24,'Forest':10,'Mountain':10,'Horta':3,'Moopsy':4,'Unexplored Territory':4,'Artificial Anomaly':4}
json.dump({'name':'t','commander':None,'startingLife':20,'mainBoard':[{**C[n],'count':k} for n,k in P.items()]},open(sys.argv[1],'w'))`;
  execFileSync(PY, ["-c", deckScript, tmp + "/deck.json", CARDS], { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "3",
    "--ai", "Michael|", "--ai-deck", tmp + "/deck.json", "--ai", "Claude|", "--ai-deck", tmp + "/deck.json", "--pilot", "Michael", "--pilot", "Claude", "--seat-rate", "0"],
    { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", TABLE_NO_VOICE: "1", TABLE_HIGHROLL: "first", TABLE_CHECKIN: "off", STRICT_SEATS: "1", HF_HUB_OFFLINE: "1", ROUTER: "code",
      REPLIES: "template", NO_GEMMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TABLE_CARDS_JSON: CARDS, TABLE_ART_DIR: tmp + "/no-art", TYPESAFE_API_KEY: "" }, stdio: "ignore" });
  let browser;
  const J = async r => { const t = await r.text(); try { return JSON.parse(t); } catch { return { error: t.slice(0, 200) }; } };
  let token = "", K = {};
  const host = async (a, b) => J(await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Brain-Token": token }, body: JSON.stringify(b) }));
  const seat = async (n, a, b = {}) => { for (let i = 0; i < 40; i++) { const r = await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[n] }, body: JSON.stringify({ seat: n, ...b }) });
    const d = await J(r);
    if (r.status === 409 && /waiting on/.test(d.error || "") && !/defenders|to block|declare blocks/.test(d.error || "")) {      // the others pass, as people would
      const p = await J(await fetch(`${BASE}/api/phase`));
      for (const w of String(Array.isArray(d.waiting) ? d.waiting.join(", ") : d.waiting || "").split(", ")) if (K[w] && w !== n)
        await fetch(`${BASE}/api/brain/pass`, { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[w] }, body: JSON.stringify({ seat: w, player: p.player, step: p.step }) });
      await sleep(250); continue; }
    return { code: r.status, ...d }; } };
  const st = async n => J(await fetch(`${BASE}/api/brain/state?seat=${n}`, { headers: { "X-Seat-Key": K[n] } }));
  const ph = async () => J(await fetch(`${BASE}/api/phase`));
  const settle = async () => { for (const n of Object.keys(K)) for (let i = 0; i < 4; i++) { const p = (await st(n)).pending_choices || []; if (!p.length) break;
    const o = { ...((p[0].slot.options || [])[0] || {}) }; delete o.label; await seat(n, "choose", { id: p[0].id, target: o }); } };
  const onto = async (n, name, keep) => { await host("search", { seat: n, name }); await host("put", { seat: n, name, quiet: true }); if (!keep) await settle();
    return (await st(n)).permanents.filter(p => p.name === name).pop().id; };
  // (a seat that holds an instant it can pay for is never passed for by the table: here, as a person would, it passes)
  const untilMain = async who => { for (let i = 0; i < 160; i++) { const p = await ph(); if (p.player === who && p.begun && p.step === "main 1") return true;
    const nxt = (p.passes || {}).next; if (nxt && K[nxt]) await seat(nxt, "pass", { player: p.player, step: p.step }); await sleep(200); } return false; };
  const newTurn = async () => { await settle(); await seat("Michael", "end", { text: "over" }); await untilMain("Claude"); await settle(); await seat("Claude", "end", { text: "over" }); await untilMain("Michael"); await settle(); };
  const perm = async id => (await st("Michael")).permanents.find(p => p.id === id);
  const untappedLands = async () => (await st("Michael")).permanents.filter(p => /Land/.test(p.type || "") && !p.tapped).length;
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    token = fs.readFileSync(tmp + "/token", "utf8").trim();
    for (const n of ["Claude", "Michael"]) K[n] = (await J(await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "e2e-" + n }, body: JSON.stringify({ name: n }) }))).key;
    await fetch(BASE + "/api/phase/windows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: false, by: "host" }) });
    await fetch(BASE + "/api/phase/next", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K.Claude }, body: JSON.stringify({ by: "Claude", key: K.Claude }) });
    await untilMain("Claude");
    await onto("Claude", "Moopsy");
    await seat("Claude", "end", { text: "over" });
    await untilMain("Michael");
    const SW = []; for (let i = 0; i < 6; i++) SW.push(await onto("Michael", "Swamp"));
    const have = async name => (await st("Michael")).permanents.filter(p => p.name === name);
    const life = async n => ((await J(await fetch(`${BASE}/api/board3d`))).seats.find(s => s.name === n) || {}).life;

    browser = await chromium.launch();
    for (const [vw, vh, label, touch] of [[1512, 860, "laptop 1512x860, mouse, the mat", false], [390, 844, "phone 390x844, real touches, the Battlefield list", true]]) {
      console.log(`\n══ ${label}`);
      const wide = vw >= 900;
      const ut = await onto("Michael", "Unexplored Territory"), aa = await onto("Michael", "Artificial Anomaly");
      await newTurn();
      const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, userAgent: "e2e-Michael", hasTouch: touch, isMobile: touch });
      const page = await ctx.newPage(), errors = [], posts = [];
      page.on("pageerror", e => errors.push(String(e)));
      page.on("request", r => { if (r.method() === "POST" && /\/api\/brain\//.test(r.url())) posts.push([new URL(r.url()).pathname.replace("/api/brain/", ""), r.postDataJSON()]); });
      const sent = a => posts.filter(p => p[0] === a);
      await page.goto(`${BASE}/hand?player=Michael#key=${encodeURIComponent(K.Michael)}`);
      await page.waitForSelector(wide ? "#mat .canvas" : "#field .card", { timeout: 15000 }); await sleep(1500);
      const reload = async () => { await page.evaluate(() => load()); await sleep(500); };
      const perm = async id => (await st("Michael")).permanents.find(p => p.id === id);
      const toast = async () => (await page.locator("#handtoast").count()) && (await page.locator("#handtoast").isVisible()) ? (await page.locator("#handtoast").textContent()) : "";
      const cdp = touch ? await ctx.newCDPSession(page) : null;
      const tapAt = async (x, y) => { await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: Math.round(x), y: Math.round(y), id: 1 }] }); await sleep(40); await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }); await sleep(400); };
      const bring = async (loc, top = 300) => { await loc.evaluate((el, top) => scrollTo(0, Math.max(0, scrollY + el.getBoundingClientRect().top - top)), top); await sleep(150); };
      const press = async loc => { if (touch) { await bring(loc); const b = await loc.boundingBox(); await tapAt(b.x + b.width / 2, b.y + b.height / 2); } else { if (await loc.evaluate(el => !el.closest("#matpop, #handtoast"))) await bring(loc, 200);       // (the hand's fan lies over the bottom of the screen: bring the card clear of it, as a player scrolls)
        const b = await loc.boundingBox(); await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2); } await sleep(700); };
      const mc = id => page.locator(`#mat .mc[data-mc="${id}"][data-mine="1"]`).first();
      const pop = page.locator("#matpop");
      const box = wide ? pop : page.locator(`#field .card[data-zid="${ut}"]`);

      if (wide) {
        console.log("1 a plain land: one click taps it, another takes it back");
        const plain = (await have("Swamp")).find(p => !p.tapped).id;
        // (identical lands share a pile; its top card is what a click acts on)
        const pile = page.locator("#mat .lstack .mc.top, #mat .mc").filter({ has: page.locator("xpath=self::*[@data-name='Swamp']") }).first();
        posts.length = 0; await press(pile);
        const tappedNow = (await have("Swamp")).filter(p => p.tapped).map(p => p.id);
        check(sent("tap").length === 1 && tappedNow.length === 1 && !(await pop.isVisible()), "a click on a Swamp taps it at once: one request, no menu (a land with only a mana ability keeps the one-click tap)", posts.map(p => p[0]));
        check(/Undo/.test(await toast()), "…and offers Undo", await toast());
        posts.length = 0; await page.locator("#handtoast [data-undotap]").click(); await sleep(900);
        check(sent("untap").length === 1 && (await have("Swamp")).every(p => !p.tapped), "Undo takes the tap back: the Swamp is untapped at the table", [posts.map(p => p[0]), await toast()]);
        posts.length = 0; await press(pile); await sleep(300);
        const tappedPile = page.locator("#mat .mc.tapped[data-name='Swamp']").first(); await press(tappedPile);
        check(sent("tap").length === 1 && sent("untap").length === 1 && (await have("Swamp")).every(p => !p.tapped), "tap it, then click the tapped land itself: it is untapped again (same step, nothing spent)", posts.map(p => p[0]));

        console.log("2 a land with an ability opens a menu instead");
        posts.length = 0; await press(mc(ut));
        check(sent("tap").length === 0 && !(await perm(ut)).tapped, "a click on Unexplored Territory does NOT tap it", posts.map(p => p[0]));
        check(await pop.isVisible() && await pop.locator(`button[data-mpool="${ut}"]`).count() === 1 && /Tap for \{C\}/.test(await pop.locator(`button[data-mpool="${ut}"]`).textContent()), "its menu offers 'Tap for {C}'", await pop.textContent());
        const ab = pop.locator(`button[data-abil="${ut}"][data-idx="0"]`);
        check(await ab.count() === 1 && /^\{1\}, \{T\}, Sacrifice this land: Search your library/.test(await ab.textContent()) && !(await ab.isDisabled()), "…and its ability, whole cost first: '{1}, {T}, Sacrifice this land: Search your library…'", await ab.textContent().catch(() => ""));
        const sel = pop.locator(`select[data-apick="${ut}-0"]`), opts = await sel.locator("option").allTextContents();
        check(opts.includes("fetch Forest") && opts.includes("fetch Swamp") && opts.every(o => /^fetch (Plains|Island|Swamp|Mountain|Forest)$/.test(o)), "…with a list of the basic lands in the library to fetch", opts);
        check(await ab.locator(".ms").count() >= 2, "the cost is drawn with symbols ({1}, {T})");

        console.log("3 the mis-tap: tapped for mana by mistake, taken back, then the ability");
        posts.length = 0; await pop.locator(`button[data-mpool="${ut}"]`).click(); await sleep(900);
        check(sent("tap").length === 1 && (await perm(ut)).tapped, "(the mistake) 'Tap for {C}' taps the Territory");
        await page.keyboard.press("Escape"); await sleep(200);
        await press(mc(ut));
        check(sent("untap").length === 1 && !(await perm(ut)).tapped, "a click on the tapped Territory takes it back: untapped at the table", [posts.map(p => p[0]), await toast()]);
        posts.length = 0; await press(mc(ut)); await pop.locator(`select[data-apick="${ut}-0"]`).selectOption("Forest"); await pop.locator(`button[data-abil="${ut}"][data-idx="0"]`).click(); await sleep(1300);
      } else {
        console.log("2 the list: the land's ability is offered in its row");
        await bring(box);
        const ab = box.locator(`button[data-abil="${ut}"][data-idx="0"]`), sel = box.locator(`select[data-apick="${ut}-0"]`);
        check(await ab.count() === 1 && /^\{1\}, \{T\}, Sacrifice this land: Search your library/.test(await ab.textContent()) && !(await ab.isDisabled()), "Unexplored Territory's row offers '{1}, {T}, Sacrifice this land: Search your library…'", await box.textContent());
        check((await sel.locator("option").allTextContents()).includes("fetch Forest"), "…with the list of basic lands to fetch");
        posts.length = 0; await sel.selectOption("Forest"); await press(ab); await sleep(900);
      }
      const act1 = sent("activate")[0];
      check(act1 && act1[1].source === "#" + ut && act1[1].index === 0 && JSON.stringify(act1[1].pick) === '["Forest"]', "pressing it sends ONE activate request, with the land chosen: {source, index 0, pick: [Forest]}", posts);
      const forest = (await have("Forest")).pop();
      check(forest && forest.tapped && !(await perm(ut)), "the table: a tapped Forest is on the battlefield, the Territory is sacrificed", (await st("Michael")).permanents.map(p => p.name + (p.tapped ? "*" : "")));
      check((await st("Michael")).permanents.filter(p => /Land/.test(p.type) && p.tapped && p.name === "Swamp").length === 1, "…and {1} was paid by the table (one Swamp tapped)");

      console.log("4 Artificial Anomaly");
      await reload(); const l0 = [await life("Michael"), await life("Claude")]; posts.length = 0;
      if (wide) { await press(mc(aa)); check(sent("tap").length === 0 && await pop.isVisible(), "a click opens its menu, and taps nothing"); await pop.locator(`button[data-abil="${aa}"][data-idx="0"]`).click(); await sleep(1300); }
      else await press(page.locator(`#field .card[data-zid="${aa}"] button[data-abil="${aa}"][data-idx="0"]`));
      await sleep(600);
      const np = (await have("New Planet token")).pop();
      check(sent("activate").length === 1 && (await life("Michael")) === l0[0] + 2 && (await life("Claude")) === l0[1] - 2, "{3}{B}, {T}, sacrifice: Claude loses 2, Michael gains 2", [posts.map(p => p[0]), l0, await life("Michael"), await life("Claude"), await page.locator("#err").textContent()]);
      check(np && np.tapped && /Land/.test(np.type) && !(await perm(aa)), "a tapped New Planet land token is on the battlefield; the Anomaly is gone", np);
      await reload();
      check(wide ? await page.locator(`#mat .mc[data-mc="${np && np.id}"]`).count() === 1 : await page.locator(`#field .card[data-zid="${np && np.id}"]`).count() === 1, "…and the page shows it");

      console.log("5 an untap the table refuses: the page says why");
      check((await ph()).player === "Michael", "(set-up) it is Michael's own turn", await ph());
      const spent = (await st("Michael")).permanents.find(p => p.name === "Swamp" && p.tapped);          // tapped by the table, to pay
      posts.length = 0;
      let asked = spent.id;
      if (wide) { await press(page.locator(`#mat .mc[data-mc="${spent.id}"]`).first()); const ub = pop.locator("button[data-tap]").first();     // (tapped Swamps share a pile: its top card is the one the menu is for)
        check(await ub.count() === 1 && /Untap/.test(await ub.textContent()), "a tapped land that is not in the page's pool opens a menu with 'Untap'", await pop.textContent());
        asked = +(await ub.getAttribute("data-tap")); await ub.click(); await sleep(900); }
      else await press(page.locator(`#field .card[data-zid="${spent.id}"] button[data-tap="${spent.id}"]`));
      check(sent("untap").length === 1 && sent("untap")[0][1].ref === "#" + asked && (await perm(asked)).tapped, "the request is sent; the table refuses; the land stays tapped", posts);
      check(/stays tapped: .*tapped by the table.*that mana is spent/.test(await toast()), "the page shows the table's reason where the player is looking: '… was tapped by the table … that mana is spent'", await toast());
      check(errors.length === 0, "no script error on the page", errors.slice(0, 3));
      await ctx.close();
      await newTurn();
    }
  } catch (e) { check(false, "the test ran", e.stack || e); }
  finally { if (browser) await browser.close(); srv.kill(); try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} }
  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join(" | ")}` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
