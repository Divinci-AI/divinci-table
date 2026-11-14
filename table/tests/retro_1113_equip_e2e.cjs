// 2026-11-13 retro, item 3: equipping from the hand page, in a real browser. A real server on a free port, temp folders, no card art.
//   laptop 1512x860: the mat. Real mouse presses (page.mouse): drag an Equipment card onto a creature; tap-then-tap; the creature's own menu.
//   phone  390x844 : the Battlefield list. REAL touches (CDP Input.dispatchTouchEvent, so Chromium's own touch pipeline makes the pointer events
//                    and applies touch-action): drag the Equipment's grip onto a creature's row; tap the grip, then tap the row.
// Every equip is checked three ways: the request the page sent (exactly one POST /api/brain/equip, with which ids), the table's state afterwards,
// and what the page told the player. Not here: a real phone, Safari / WebKit, Firefox, a pen.
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/retro_1113_equip_e2e.cjs
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
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "retro1113eq-"));
  const deckScript = `import json,os,sys
sys.path.insert(0,'table')
import make_deck_json_shim as s
C={c['name']:c for c in s.load(sys.argv[2])}
P={'Plains':14,'Forest':14,'Mountain':20,'Horta':3,'Ghost of Illyria':3,'Targ Wrangler':3,'Type-2 Phaser':3,'Tricorder':3,'Moopsy':3}
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
    const d = await J(r); if (r.status === 409 && /waiting on/.test(d.error || "")) { await sleep(250); continue; } return { code: r.status, ...d }; } };
  const st = async n => J(await fetch(`${BASE}/api/brain/state?seat=${n}`, { headers: { "X-Seat-Key": K[n] } }));
  const ph = async () => J(await fetch(`${BASE}/api/phase`));
  const settle = async () => { for (const n of Object.keys(K)) for (let i = 0; i < 4; i++) { const p = (await st(n)).pending_choices || []; if (!p.length) break;
    const o = { ...((p[0].slot.options || [])[0] || {}) }; delete o.label; await seat(n, "choose", { id: p[0].id, target: o }); } };
  const onto = async (n, name, keep) => { await host("search", { seat: n, name }); await host("put", { seat: n, name, quiet: true }); if (!keep) await settle();
    return (await st(n)).permanents.filter(p => p.name === name).pop().id; };
  const untilMain = async who => { for (let i = 0; i < 80; i++) { const p = await ph(); if (p.player === who && p.begun && p.step === "main 1") return true; await sleep(250); } return false; };
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
    await onto("Claude", "Moopsy"); await onto("Claude", "Horta");      // two creatures over there: the Phaser's enters trigger has a real choice
    await seat("Claude", "end", { text: "over" });
    await untilMain("Michael");
    const LANDS = []; for (const n of ["Mountain", "Mountain", "Mountain", "Plains", "Forest"]) LANDS.push(await onto("Michael", n));
    const ghost = await onto("Michael", "Ghost of Illyria"), targ = await onto("Michael", "Targ Wrangler");
    const tric = await onto("Michael", "Tricorder");
    const phaser = await onto("Michael", "Type-2 Phaser", true);                     // keep: its enters trigger leaves Michael a choice (used below)
    const eq = async (g, c) => seat("Michael", "equip", { equipment: "#" + g, creature: "#" + c });

    browser = await chromium.launch();
    for (const [vw, vh, label, touch] of [[1512, 860, "laptop 1512x860, mouse, the mat", false], [390, 844, "phone 390x844, real touches, the Battlefield list", true]]) {
      console.log(`\n══ ${label}`);
      const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, userAgent: "e2e-Michael", hasTouch: touch, isMobile: touch });
      const page = await ctx.newPage(), errors = [], posts = [];
      page.on("pageerror", e => errors.push(String(e)));
      page.on("request", r => { if (r.method() === "POST" && /\/api\/brain\//.test(r.url())) posts.push([new URL(r.url()).pathname.replace("/api/brain/", ""), r.postDataJSON()]); });
      const equips = () => posts.filter(p => p[0] === "equip").map(p => `${p[1].equipment}>${p[1].creature}`);
      const sent = a => posts.filter(p => p[0] === a).length;
      await page.goto(`${BASE}/hand?player=Michael#key=${encodeURIComponent(K.Michael)}`);
      const wide = vw >= 900;
      await page.waitForSelector(wide ? "#mat .canvas" : "#field .card", { timeout: 15000 }); await sleep(1500);
      const reload = async () => { await page.evaluate(() => load()); await sleep(450); };
      const card = id => wide ? page.locator(`#mat .mc[data-mc="${id}"][data-mine="1"]`).first() : page.locator(`#field .card[data-zid="${id}"]`).first();
      const lit = async () => (await page.$$eval(".eqdrop", els => els.filter(e => e.offsetParent !== null).map(e => +(e.dataset.mc || e.dataset.zid)))).sort((a, b) => a - b).join();
      const over = async () => (await page.$$eval(".eqover", els => els.filter(e => e.offsetParent !== null).map(e => +(e.dataset.mc || e.dataset.zid)))).join();
      const toast = async () => (await page.locator("#handtoast").count()) && (await page.locator("#handtoast").isVisible()) ? (await page.locator("#handtoast").textContent()) : "";
      const mid = async loc => { const b = await loc.boundingBox(); return [b.x + b.width / 2, b.y + b.height / 2]; };
      const bring = async (loc, top = 300) => { await loc.evaluate((el, top) => scrollTo(0, Math.max(0, scrollY + el.getBoundingClientRect().top - top)), top); await sleep(150); };
      const cdp = touch ? await ctx.newCDPSession(page) : null;
      const T = async (type, x, y) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x: Math.round(x), y: Math.round(y), id: 1 }] });
      const tapAt = async (x, y) => { await T("touchStart", x, y); await sleep(40); await T("touchEnd"); await sleep(350); };
      const grip = id => page.locator(`${wide ? "#matpop" : "#field"} [data-eqgrip="${id}"]`).first();
      // drag: press the Equipment (the card on the mat; its grip in the list), move in steps, and stop OVER the creature without letting go
      let finger = [0, 0];
      const dragTo = async (gearId, creId) => {
        if (wide) { const [x0, y0] = await mid(card(gearId)), [x1, y1] = await mid(card(creId)); await page.mouse.move(x0, y0); await page.mouse.down(); await page.mouse.move(x0 + 8, y0 + 8, { steps: 2 }); await page.mouse.move(x1, y1, { steps: 12 }); await sleep(120); return; }
        await bring(grip(gearId), 420); const [x0, y0] = await mid(grip(gearId)); await T("touchStart", x0, y0); await sleep(60);
        let x = x0, y = y0; const step = async (nx, ny) => { for (let i = 1; i <= 6; i++) { await T("touchMove", x + (nx - x) * i / 6, y + (ny - y) * i / 6); await sleep(16); } x = nx; y = ny; finger = [x, y]; };
        await step(x0 + 4, y0 + 30);
        for (let i = 0; i < 60; i++) {                          // the row may be off the screen: hold the finger at the edge and the page scrolls to it
          const b = await card(creId).boundingBox();
          if (b && b.y + b.height / 2 > 100 && b.y + b.height / 2 < vh - 100) { await step(b.x + b.width / 2, b.y + b.height / 2); break; }
          await step(vw / 2, b && b.y < 100 ? 24 : vh - 24); await sleep(120);
        }
        await sleep(150);
      };
      const drop = async () => { if (wide) await page.mouse.up(); else await T("touchEnd"); await sleep(900); };

      // ── 0. the table is waiting on this seat for a choice (Type-2 Phaser's enters trigger): nothing is equipped, nothing is paid
      if ((await st("Michael")).pending_choices?.length) {
        console.log("0 a choice is pending: an equip is not sent");
        await reload(); posts.length = 0; const u0 = await untappedLands();
        await dragTo(tric, targ); await drop();
        check(equips().length === 0 && (await perm(tric)).attached_to == null && (await untappedLands()) === u0, "dropping the Tricorder on Targ Wrangler sends no equip while the table waits on a choice", equips());
        check(/waiting on you first/.test(await toast()), "…and the page says why, with a way to the choice", await toast());
        await settle(); await page.evaluate(() => { try { localStorage.removeItem("matpos:Michael"); } catch {} }); await page.reload(); await page.waitForSelector(wide ? "#mat .canvas" : "#field .card"); await sleep(1200);
      }
      await newTurn(); await reload();
      // the board from the report: Type-2 Phaser on Ghost of Illyria, Tricorder loose, Targ Wrangler new
      if ((await perm(phaser)).attached_to !== ghost) { const r = await eq(phaser, ghost); if (r.code !== 200) throw new Error("set-up equip: " + JSON.stringify(r)); await newTurn(); }
      if ((await perm(tric)).attached_to === targ) { const r = await eq(tric, ghost); if (r.code !== 200) throw new Error("set-up equip: " + JSON.stringify(r)); await newTurn(); }
      await reload(); await sleep(600);

      console.log("1 drag the Tricorder onto Targ Wrangler");
      posts.length = 0; errors.length = 0;
      const tricOn = (await perm(tric)).attached_to;             // (loose on the first pass; on Ghost of Illyria on the second, left there by the first)
      const posBefore = wide ? await page.evaluate(() => localStorage.getItem("matpos:Michael") || "{}") : "";
      await dragTo(tric, targ);
      check((await lit()) === [ghost, targ].filter(id => id !== tricOn).sort((a, b) => a - b).join(), `while it is dragged, exactly the creatures it may go onto are lit (${tricOn == null ? "Ghost of Illyria and Targ Wrangler" : "Targ Wrangler; it is on Ghost of Illyria"}): no land, no Equipment, nothing of the opponent's`, await lit());
      check((await over()) === String(targ), "the creature under the pointer is lit solid", await over());
      if (!wide) { check(await page.locator(".eqghost").count() === 1 && /Tricorder/.test(await page.locator(".eqghost").textContent()), "a label with the Equipment's name follows the finger");
        check(sent("tap") === 0 && sent("untap") === 0, "the drag itself sends nothing"); }
      await drop();
      check(equips().join("|") === `#${tric}>#${targ}`, "letting go sends ONE equip request, the same one the button sends: {equipment: Tricorder, creature: Targ Wrangler}", posts);
      check((await perm(tric)).attached_to === targ, "the table has the Tricorder on Targ Wrangler", await perm(tric));
      check(/I pay \{1\} to equip Tricorder to Targ Wrangler/.test(await toast()), "the page shows the table's own words for it", await toast());
      check(await page.locator(".eqdrop, .eqover, .eqghost").count() === 0, "the lights go out afterwards");
      if (wide) check((await page.evaluate(() => localStorage.getItem("matpos:Michael") || "{}")) === posBefore, "the Equipment card was not moved by the drop (no saved spot for it): it goes back to its place");
      check(sent("tap") === 0, "no stray tap was sent", posts.map(p => p[0]));

      console.log("2 re-equip: drag the Type-2 Phaser from Ghost of Illyria onto Targ Wrangler");
      posts.length = 0; const u2 = await untappedLands();
      await dragTo(phaser, targ);
      check((await lit()) === String(targ), "only Targ Wrangler is lit: not the creature the Phaser is already on", await lit());
      await drop();
      check(equips().join("|") === `#${phaser}>#${targ}` && (await perm(phaser)).attached_to === targ, "it moves to Targ Wrangler", [equips(), await perm(phaser)]);
      check((await untappedLands()) === u2 - 3, "…and the move was paid for by the table ({3})", [u2, await untappedLands()]);

      console.log("3 no mana left: the table says so, the page asks for nothing");
      for (const p of (await st("Michael")).permanents) if (/Land/.test(p.type || "") && !p.tapped) await seat("Michael", "tap", { ref: "#" + p.id });
      posts.length = 0; await reload();
      const g3 = await perm(tric);
      check(g3.equip_can === false && /^needs \{1\}/.test(g3.equip_why || ""), "(set-up) every land is tapped: the table's state says the Tricorder 'needs {1}'", [g3.equip_can, g3.equip_why]);
      if (!wide) { await bring(card(tric), 300);
        check(/Can't equip now: needs \{1\}/.test(await card(tric).textContent()) && await grip(tric).count() === 0, "the Tricorder's row shows the table's reason, and offers no grip and no button to press", await card(tric).textContent()); }
      else {
        await dragTo(tric, ghost);
        check((await lit()) === "", "nothing is lit");
        check(/Can't equip Tricorder now: needs \{1\}/.test(await toast()), "the page shows the table's reason as soon as the drag starts", await toast());
        await drop();
        check(equips().length === 0 && (await perm(tric)).attached_to === targ, "letting go over a creature sends no equip", equips());
        if (wide) check((await page.evaluate(() => localStorage.getItem("matpos:Michael") || "{}")).includes(`Michael:${tric}`), "on the mat it is simply the move it always was (the card stays where it was dropped)");
        if (wide) { await page.evaluate(() => { localStorage.removeItem("matpos:Michael"); }); await page.reload(); await page.waitForSelector("#mat .canvas"); await sleep(1200); }
      }

      console.log("4 tap, then tap (no drag)");
      await newTurn(); await reload(); posts.length = 0;
      const openGear = async id => { if (wide) { const [x, y] = await mid(card(id)); await page.mouse.click(x, y); await sleep(300); } else await bring(grip(id), 400); };
      const pressGrip = async id => { const [x, y] = await mid(grip(id)); if (wide) await page.mouse.click(x, y); else await tapAt(x, y); await sleep(350); };
      const pressCard = async id => { if (!wide) await bring(card(id), 300); const b = await card(id).boundingBox(); const x = b.x + (wide ? b.width / 2 : 24), y = b.y + (wide ? b.height / 2 : 14);
        if (wide) await page.mouse.click(x, y); else await tapAt(x, y); await sleep(900); };
      await openGear(tric);
      check(await grip(tric).count() === 1 && await grip(tric).isVisible(), "the Equipment shows a grip: 'Drag onto a creature · or tap, then tap the creature'", [(await perm(tric)).equip_can, (await perm(tric)).equip_why, (await ph()).step, wide ? (await page.locator("#matpop").innerHTML()).slice(-420) : ""]);
      await pressGrip(tric);
      check((await lit()) === String(ghost) && /tap one of your highlighted creatures/.test(await toast()), "a tap on the grip lights the creatures it may go onto (Ghost of Illyria; it is on Targ Wrangler) and says what to do", [await lit(), await toast()]);
      check(equips().length === 0, "nothing is sent yet");
      // a tap somewhere else only cancels: here on a land (the mat) / on the page heading (the list)
      if (wide) { const [x, y] = await mid(page.locator(`#mat .mc[data-mc="${LANDS[4]}"]`).first()); await page.mouse.click(x, y); } else { await bring(page.locator("#fieldpanel h2"), 320); const b = await page.locator("#fieldpanel h2").boundingBox(); await tapAt(b.x + 30, b.y + 8); }
      await sleep(500);
      check((await lit()) === "" && equips().length === 0 && sent("tap") === 0 && !(await perm(LANDS[4])).tapped, "a tap elsewhere cancels and does nothing else (the land under it is NOT tapped)", [await lit(), posts.map(p => p[0])]);
      await openGear(tric); await pressGrip(tric);
      await page.keyboard.press("Escape"); await sleep(250);
      check((await lit()) === "", "Escape cancels too");
      await openGear(tric); await pressGrip(tric); posts.length = 0;
      await pressCard(ghost);
      check(equips().join("|") === `#${tric}>#${ghost}` && (await perm(tric)).attached_to === ghost, "tap the grip, tap Ghost of Illyria: one equip request, and the Tricorder is on it", [equips(), await perm(tric)]);
      check(sent("tap") === 0 && (wide ? !(await page.locator("#matpop").isVisible()) : true), "the tap that chose the creature did nothing else (no tap sent, its menu did not open)", posts.map(p => p[0]));

      console.log("5 from the creature's side, and with the lands in the page's mana pool");
      await newTurn(); await reload(); posts.length = 0;
      if (wide) {
        const [x, y] = await mid(card(ghost)); await page.mouse.click(x, y); await sleep(350);
        const b = page.locator(`#matpop button[data-equip="${phaser}"][data-eqon="${ghost}"]`);
        check(await b.count() === 1 && /Equip Type-2 Phaser \{3\} \(moves it here\)/.test(await b.textContent()), "Ghost of Illyria's own menu offers 'Equip Type-2 Phaser {3} (moves it here)'", await page.locator("#matpop").innerHTML());
        check(/Tricorder is on it/.test(await page.locator("#matpop").textContent()), "…and says the Tricorder is on it already");
        await page.keyboard.press("Escape"); await page.mouse.click(5, vh - 200); await sleep(200);
        // tap three Mountains into the pool, the way a paper player does before paying
        // (identical lands share one pile on the mat, so this calls the function a click on a land calls, once per Mountain)
        await page.evaluate(async ids => { for (const id of ids) await poolTap(S.permanents.find(p => p.id === id), "R"); }, LANDS.slice(0, 3)); await sleep(500);
      } else {
        const b = page.locator(`#field .card[data-zid="${ghost}"] button[data-equip="${phaser}"][data-eqon="${ghost}"]`);
        check(await b.count() === 1 && /Equip Type-2 Phaser \{3\} \(moves it here\)/.test(await b.textContent()), "Ghost of Illyria's own row offers 'Equip Type-2 Phaser {3} (moves it here)'");
        for (const id of LANDS.slice(0, 3)) await seat("Michael", "tap", { ref: "#" + id });       // (the list has no pool: the lands are simply tapped)
      }
      await reload(); await sleep(300);
      const g5 = await perm(phaser);
      check(g5.equip_can === false && /^needs \{3\}/.test(g5.equip_why || ""), "(set-up) three lands are tapped: the table's state says the Phaser 'needs {3}'", g5);
      if (wide) {
        check(await page.evaluate(() => { poolLoad(); return MPOOL.items.length; }) === 3, "(set-up) the page's pool holds the three lands");
        posts.length = 0;
        await dragTo(phaser, ghost);
        check((await lit()) === String(ghost), "the Phaser still lights its target: pooled mana is given back before the table pays (this is where the buttons used to vanish)", [await lit(), await toast()]);
        await drop(); await sleep(600);
        check(sent("untap") === 3 && equips().join("|") === `#${phaser}>#${ghost}` && (await perm(phaser)).attached_to === ghost, "dropping it gives the pool back (3 untaps), then sends the equip; the table pays and the Phaser is on Ghost of Illyria", posts.map(p => p[0]));
      } else {
        await bring(card(phaser), 300);
        check(/Can't equip now: needs \{3\}/.test(await card(phaser).textContent()) && await grip(phaser).count() === 0, "in the list (no pool there) the row shows the table's reason and no grip");
      }

      console.log("6 the table refuses: its reason is shown");
      await newTurn(); await reload(); posts.length = 0;
      const was6 = (await perm(phaser)).attached_to, to6 = was6 === ghost ? targ : ghost;
      await dragTo(phaser, to6);                                  // the page believes it can: lit
      check((await lit()) === String(to6), "(set-up) lit: the page was told it can be paid for", await lit());
      for (const id of LANDS) await seat("Michael", "tap", { ref: "#" + id });       // …and the mana goes behind the page's back before the drop
      await drop(); await sleep(500);
      check(equips().join("|") === `#${phaser}>#${to6}` && (await perm(phaser)).attached_to === was6, "the request is sent; the table refuses; nothing moved", [equips(), (await perm(phaser)).attached_to]);
      check(/Not equipped: can't pay \{3\} to equip Type-2 Phaser/.test(await toast()), "the page shows the table's refusal, in the table's words", await toast());
      check(await page.locator(".eqdrop, .eqover, .eqghost").count() === 0, "…and the lights are out");

      if (!wide) {
        console.log("7 the list still scrolls, and an ordinary touch on a row is not a drag");
        await newTurn(); await reload(); posts.length = 0;
        await page.evaluate(() => scrollTo(0, 0)); await sleep(200);
        const row = card(targ); await bring(row, 500); const y0 = await page.evaluate(() => scrollY); const b = await row.boundingBox();
        await T("touchStart", b.x + 30, b.y + 12); for (let i = 1; i <= 8; i++) { await T("touchMove", b.x + 30, b.y + 12 - i * 25); await sleep(16); } await T("touchEnd"); await sleep(500);
        check((await page.evaluate(() => scrollY)) > y0 + 60, "a finger dragged up on a creature's row scrolls the page as before", [y0, await page.evaluate(() => scrollY)]);
        check(posts.length === 0 && (await lit()) === "", "…and sends nothing");
        await bring(grip(tric), 420); const ys = await page.evaluate(() => scrollY); const [gx, gy] = await mid(grip(tric));
        await T("touchStart", gx, gy); for (let i = 1; i <= 6; i++) { await T("touchMove", gx + i * 6, gy - i * 12); await sleep(16); } await sleep(100);
        check((await page.evaluate(() => scrollY)) === ys && await page.locator(".eqghost").count() === 1, "a finger dragged from the GRIP does not scroll the page (touch-action: none): it carries the Equipment", [ys, await page.evaluate(() => scrollY)]);
        await T("touchMove", 6, 300); await sleep(60); await T("touchEnd"); await sleep(400);
        check(equips().length === 0 && await page.locator(".eqghost, .eqdrop").count() === 0, "let go over nothing: no equip, the label and the lights are gone", equips());
      }
      check(errors.length === 0, "no script error on the page", errors.slice(0, 3));
      await ctx.close();
    }
  } catch (e) { check(false, "the test ran", e.stack || e); }
  finally { if (browser) await browser.close(); srv.kill(); try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} }
  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join(" | ")}` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
