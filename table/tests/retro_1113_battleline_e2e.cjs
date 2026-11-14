// 2026-11-13, item 5: the battle line on the hand page, in a real browser. A real server on a free port, temp folders, no card art.
// Attackers and blockers are declared through the seats' own keys (the table's API); the PAGE is only looked at: where the cards are drawn.
//   1512x860 and 1280x1080 (the stream's size for one table view): the mat.   390x844: the Battlefield list's lanes.
// Checked: a declared attacker leaves its row for the line (and slides there); a blocker stands on its own side, opposite its attacker; two
// blockers side by side; "unblocked" once blocks are in; a blocker that dies mid-combat leaves the line; everyone is back in the row when the
// combat is over; the same mirrored when the OTHER seat attacks; nothing is drawn outside the board; a card on the line still opens its menu;
// a tapped attacker is still drawn turned; prefers-reduced-motion switches the slide off.
// Not here: /board and /watch (not changed: see docs/retro-20261113-battle-line.md), a real phone, Safari / WebKit, Firefox.
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/retro_1113_battleline_e2e.cjs
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
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "retro1113bl-"));
  const deckScript = `import json,os,sys
sys.path.insert(0,'table')
import make_deck_json_shim as s
C={c['name']:c for c in s.load(sys.argv[2])}
P={'Plains':14,'Forest':14,'Mountain':20,'Horta':3,'Ghost of Illyria':3,'Xindi Surveyors':4,'Pet Sehlat':3,'Moopsy':4,'Plasma Cascade':4}
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
    const cMoopsy = await onto("Claude", "Moopsy"), cXindi = await onto("Claude", "Xindi Surveyors"), cPet = await onto("Claude", "Pet Sehlat");
    await seat("Claude", "end", { text: "over" });
    await untilMain("Michael");
    const LANDS = []; for (const n of ["Mountain", "Mountain", "Mountain", "Mountain", "Mountain", "Mountain"]) LANDS.push(await onto("Michael", n));
    const horta = await onto("Michael", "Horta"), xindi = await onto("Michael", "Xindi Surveyors"), ghost = await onto("Michael", "Ghost of Illyria");
    const hold = async () => { const h = (await st("Michael")).hand.filter(c => c.name === "Plasma Cascade").length; for (let i = h; i < 2; i++) await host("search", { seat: "Michael", name: "Plasma Cascade" }); };
    const combat = async () => ((await ph()).combat || {}).attacks || [];
    const stage = async who => ((await combat()).find(a => a.attacker === who) || {}).stage;

    browser = await chromium.launch();
    const VIEWS = [[1512, 860, "laptop 1512x860, the mat"], [1280, 1080, "1280x1080 (one table view on the stream), the mat"], [390, 844, "phone 390x844, the Battlefield list"]];
    for (const [vw, vh, label] of VIEWS) {
      console.log(`\n══ ${label}`);
      const wide = vw >= 900;
      await newTurn(); await hold();
      // (each pass needs the same board: bring back what the last pass's combat killed)
      const have = async (n, name) => (await st(n)).permanents.filter(p => p.name === name).map(p => p.id);
      const mH = (await have("Michael", "Horta"))[0] ?? await onto("Michael", "Horta"), mX = (await have("Michael", "Xindi Surveyors"))[0] ?? await onto("Michael", "Xindi Surveyors");
      const cM = (await have("Claude", "Moopsy"))[0] ?? await onto("Claude", "Moopsy"), cX = (await have("Claude", "Xindi Surveyors"))[0] ?? await onto("Claude", "Xindi Surveyors");
      await newTurn(); await hold();                               // nobody is summoning sick; Michael holds an instant he can pay for (the window after blocks stays open)
      const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, userAgent: "e2e-Michael", hasTouch: !wide, isMobile: !wide });
      const page = await ctx.newPage(), errors = [];
      page.on("pageerror", e => errors.push(String(e)));
      await page.goto(`${BASE}/hand?player=Michael#key=${encodeURIComponent(K.Michael)}`);
      await page.waitForSelector(wide ? "#mat .canvas" : "#field .card", { timeout: 15000 }); await sleep(1500);
      const reload = async () => { await page.evaluate(async () => { await load(); await refreshPhase(); B3 = await (await fetch("/api/board3d", { cache: "no-store" })).json(); matSig = ""; draw(); }); await sleep(500); };
      // where a card is drawn: its box as laid out (left/top of the element, before any turn for "tapped")
      const box = (id, mine = true) => page.evaluate(([id, mine]) => { const el = document.querySelector(`#mat .mc[data-mc="${id}"]${mine ? '[data-mine="1"]' : ':not([data-mine="1"])'}`); if (!el) return null;
        const cv = el.closest(".canvas").getBoundingClientRect(), cs = getComputedStyle(el);
        return { x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight, bl: el.dataset.bl || "", cls: el.className, cvW: cv.width, cvH: cv.height, tf: cs.transform, anim: cs.animationName, by: el.style.getPropertyValue("--by") }; }, [id, mine]);
      const badges = () => page.$$eval("#mat .blb", els => els.map(e => e.textContent));
      // every slide the page starts is noted as it starts (the mat is rebuilt often, so the class may be gone by the time one looks)
      await page.evaluate(() => { window.__slides = []; document.addEventListener("animationstart", e => { if (e.animationName === "blmove") window.__slides.push({ id: +e.target.dataset.mc, by: parseFloat(e.target.style.getPropertyValue("--by")), bx: parseFloat(e.target.style.getPropertyValue("--bx")) }); }, true); });
      const slides = async () => { const v = await page.evaluate(() => { const s = window.__slides; window.__slides = []; return s; }); return v; };
      const inside = b => b && b.x >= 0 && b.y >= 0 && b.x + b.w <= b.cvW + 0.5 && b.y + b.h <= b.cvH + 0.5;
      const strip = () => page.evaluate(() => { const el = document.getElementById("blstrip"); return { text: el ? el.innerText : "", lanes: [...document.querySelectorAll("#blstrip .bllane")].map(l => ({ id: +l.dataset.blLane, unb: l.classList.contains("unb"), blockers: [...l.querySelectorAll("[data-bl-blocker]")].map(b => +b.dataset.blBlocker), right: l.getBoundingClientRect().right, left: l.getBoundingClientRect().left })), docW: document.documentElement.scrollWidth, vw: innerWidth }; });

      console.log("1 before the attack");
      await reload();
      const h0 = wide ? await box(mH) : null, x0 = wide ? await box(mX) : null, cm0 = wide ? await box(cM, false) : null, cx0 = wide ? await box(cX, false) : null;
      if (wide) { check(h0 && x0 && cm0 && cx0 && !h0.bl && !x0.bl && !cm0.bl && (await badges()).length === 0, "everyone is in their row; nobody is on the line", [h0, cm0]);
        check(/the battle line/.test(await page.locator("#mat .mid").textContent()), "the band between the two sides says 'the battle line'"); }
      else check((await strip()).lanes.length === 0 && !(await page.locator("#blstrip").isVisible()), "no battle line is shown in the list");

      console.log("2 Michael attacks with Horta and Xindi Surveyors");
      let r = await seat("Michael", "attack", { assign: { ["#" + mH]: "Claude", ["#" + mX]: "Claude" } });
      check(r.code === 200 && (await stage("Michael")) === "blocks", "(set-up) the attack is declared; the table waits for Claude's blocks", [r.code, r.error, await stage("Michael")]);
      await reload();
      let h1, x1;
      if (wide) { h1 = await box(mH); x1 = await box(mX);
        check(h1.bl === "attacker" && x1.bl === "attacker", "both attackers are on the line", [h1.cls, x1.cls]);
        check(h1.y < h0.y - 20 && x1.y < x0.y - 20 && h1.y === x1.y, "they have stepped out of their row TOWARDS the other side (up, by more than 20 px), level with each other", [h0.y, h1.y, x0.y, x1.y]);
        check((h1.x < x1.x) === (h0.x < x0.x) && h1.x >= (await page.evaluate(() => ROWX0)) - 0.5, "…in the same order along the row as before (a tapped card takes more room in its row, on the line as off it)", [h0.x, h1.x, x0.x, x1.x]);
        const sl = await slides(), sh = sl.find(s => s.id === mH);
        check(sh && sh.by > 20 && sl.some(s => s.id === mX), "the step is a short slide from where the card was: a CSS animation started on both, beginning at the old place (--by is the way back to the row)", sl);
        check(/tapped/.test(h1.cls) && /matrix/.test(h1.tf), "an attacker that tapped to attack is still drawn turned on its side", [h1.cls, h1.tf]);
        check(inside(h1) && inside(x1), "both are inside the board", [h1, x1]);
        const bd = await badges(); check(bd.length === 2 && bd.every(t => t === "⚔ → Claude"), "each says whom it attacks; nobody is 'unblocked' before the blocks are in", bd);
        check((await box(cM, false)).y === cm0.y && !(await box(cM, false)).bl, "the other side has not moved yet");
        const [mx, my] = [h1.x + h1.w / 2, h1.y + h1.h / 2]; const cvb = await page.locator("#mat .canvas").boundingBox(); await page.mouse.click(cvb.x + mx, cvb.y + my); await sleep(350);
        check(await page.locator("#matpop").isVisible() && /Horta/.test(await page.locator("#matpop .nm").textContent()), "a card on the line still opens its menu when pressed", await page.locator("#matpop").textContent());
        await page.evaluate(() => { document.getElementById("matpop").style.display = "none"; matSel = null; matSig = ""; drawMat(); }); await sleep(150);   // (close it without pressing anything else)
      } else { const s = await strip();
        check(s.lanes.length === 2 && s.lanes[0].id === mH && s.lanes[1].id === mX && s.lanes.every(l => !l.unb && !l.blockers.length), "the list shows the line: two lanes, Horta and Xindi Surveyors, no blockers yet", s);
        check(/you attack Claude/.test(s.text) && /blocks are not in yet/.test(s.text), "…each says whom it attacks and that the blocks are not in", s.text);
        check(s.docW <= s.vw && s.lanes.every(l => l.left >= 0 && l.right <= s.vw), "nothing is pushed off the screen (no sideways scroll)", s); }

      if (wide) await slides();
      console.log("3 Claude blocks Horta with Moopsy and Xindi Surveyors; Michael's Xindi Surveyors is not blocked");
      r = await seat("Claude", "block", { blocks: { ["#" + mH]: ["#" + cM, "#" + cX], ["#" + mX]: [] } });
      check(r.code === 200 && (await stage("Michael")) === "window", "(set-up) the blocks are in; the window before damage is open (Michael holds an instant)", [r.code, r.error, await stage("Michael")]);
      await reload();
      if (wide) { const cm1 = await box(cM, false), cx1 = await box(cX, false); h1 = await box(mH); x1 = await box(mX);
        check(cm1.bl === "blocker" && cx1.bl === "blocker", "both blockers are on the line");
        const sl3 = await slides(); check(sl3.some(s => s.id === cM && s.by < -20) && sl3.some(s => s.id === cX), "…and slid there from their row", sl3);
        check(cm1.y > cm0.y + 20 && cx1.y > cx0.y + 20 && cm1.y === cx1.y, "they have stepped out of THEIR row towards the attackers (down), level with each other", [cm0.y, cm1.y]);
        check(cm1.y + cm1.h <= h1.y + 8, "the two sides face each other across the line without covering each other", [cm1.y + cm1.h, h1.y]);
        const mid = (cm1.x + cx1.x + cm1.w) / 2, hc = h1.x + h1.w / 2;
        check(Math.abs(mid - hc) <= 2 || cm1.x <= 0.5 + (await page.evaluate(() => ROWX0)), "the pair of blockers is centred on the attacker it blocks (Horta)", [mid, hc]);
        check(Math.abs(cx1.x - cm1.x) >= cm1.w, "two blockers on one attacker stand side by side, not on top of each other", [cm1.x, cx1.x, cm1.w]);
        check(/bl-unb/.test(x1.cls) && /bl-blkd/.test(h1.cls), "the attacker nobody blocked is marked unblocked; the blocked one is marked blocked", [x1.cls, h1.cls]);
        const bd = await badges(); check(bd.includes("⚔ unblocked") && bd.includes("⚔ ⛨2") && bd.filter(t => /^⛨ Horta/.test(t)).length === 2, "the labels: '⚔ unblocked', '⚔ ⛨2', and each blocker names Horta", bd);
        check(inside(cm1) && inside(cx1) && inside(h1) && inside(x1), "all four are inside the board");
        const ov = await page.evaluate(() => { const r = [...document.querySelectorAll("#mat .mc.bl")].map(e => e.getBoundingClientRect()); const cv = document.querySelector("#mat .canvas").getBoundingClientRect(); return r.every(b => b.left >= cv.left - 1 && b.right <= cv.right + 1 && b.top >= cv.top - 1 && b.bottom <= cv.bottom + 1); });
        check(ov, "…also as drawn (a turned card's drawn box included)");
        if (vw === 1280) { await page.emulateMedia({ reducedMotion: "reduce" }); await page.evaluate(() => { BL_PREV = new Map([...BL_PREV].map(([k, v]) => [k, { ...v, line: !v.line, y: v.y + 30 }])); matSig = ""; drawMat(); }); await sleep(150);
          const an = await page.evaluate(() => [...document.querySelectorAll("#mat .mc.bl-move")].map(e => getComputedStyle(e).animationName));
          check(an.length > 0 && an.every(a => a === "none"), "with prefers-reduced-motion the cards are placed without the slide", an); await page.emulateMedia({ reducedMotion: "no-preference" }); }
      } else { const s = await strip();
        check(s.lanes.length === 2 && s.lanes[0].blockers.join() === [cM, cX].join() && !s.lanes[0].unb, "Horta's lane lists its two blockers under it", s.lanes);
        check(s.lanes[1].unb && /unblocked/.test(s.text), "Xindi Surveyors' lane is marked unblocked", s.text);
        check(s.docW <= s.vw && s.lanes.every(l => l.left >= 0 && l.right <= s.vw), "nothing is pushed off the screen", s); }

      console.log("4 a blocker leaves the combat (Plasma Cascade kills Claude's Xindi Surveyors)");
      r = await seat("Michael", "cast", { name: "Plasma Cascade", targets: [{ at: "Claude", ref: "#" + cX }] });
      await settle();
      const goneX = !(await st("Claude")).permanents.some(p => p.id === cX);
      check(r.code === 200 && goneX && (await stage("Michael")) === "window", "(set-up) the instant resolved, the blocker is in the graveyard, the combat goes on", [r.code, r.error, goneX, await stage("Michael")]);
      await reload();
      if (wide) { const cm2 = await box(cM, false), h2 = await box(mH);
        check((await box(cX, false)) === null && cm2.bl === "blocker", "the dead blocker is gone; the other is still on the line");
        check(Math.abs(cm2.x + cm2.w / 2 - (h2.x + h2.w / 2)) <= 2, "…and now stands exactly opposite Horta", [cm2.x, h2.x]);
        check((await badges()).includes("⚔ ⛨1"), "Horta's label counts one blocker", await badges());
      } else { const s = await strip(); check(s.lanes[0].blockers.join() === String(cM), "Horta's lane lists the one blocker left", s.lanes); }

      if (wide) await slides();
      console.log("5 the combat is over");
      for (let i = 0; i < 12 && (await stage("Michael")) !== "done"; i++) { await seat("Michael", "next"); await settle(); await sleep(250); }
      check((await stage("Michael")) === "done", "(set-up) the damage has been dealt", await combat());
      await reload();
      if (wide) { const x3 = await box(mX), h3 = await box(mH);
        check((await page.locator("#mat .mc.bl").count()) === 0 && (await badges()).length === 0, "nobody is on the line any more, no labels");
        check(x3 && Math.abs(x3.y - x0.y) < 1 && Math.abs(x3.x - x0.x) < 1, "the surviving attacker is back in its row, at its own place", [x0, x3]);
        const sl5 = await slides(); check(sl5.some(s => s.id === mX && s.by < -20), "…and slid back there (an animation that begins on the line)", sl5);
        check(h3 === null || Math.abs(h3.y - h0.y) < 1, "Horta (deathtouch from Moopsy) is " + (h3 === null ? "in the graveyard" : "back in its row"));
        check(/the battle line/.test(await page.locator("#mat .mid").textContent()) || (await page.locator("#mat .mid button").count()) > 0, "the band is the plain battle line again");
      } else check((await strip()).lanes.length === 0 && !(await page.locator("#blstrip").isVisible()), "the list's battle line is gone");

      if (vw === 1512) {
        console.log("6 the other way round: Claude attacks Michael, Michael blocks");
        await seat("Michael", "end", { text: "over" }); await untilMain("Claude"); await settle(); await hold();
        const cP = (await have("Claude", "Pet Sehlat"))[0], cX2 = (await have("Claude", "Xindi Surveyors"))[0] ?? await onto("Claude", "Xindi Surveyors");
        await seat("Claude", "end", { text: "over" }); await untilMain("Michael"); await settle(); await seat("Michael", "end", { text: "over" }); await untilMain("Claude"); await settle();
        await reload(); const p0 = await box(cP, false), g0 = await box(ghost);
        r = await seat("Claude", "attack", { assign: { ["#" + cP]: "Michael", ["#" + cX2]: "Michael" } });
        check(r.code === 200 && (await stage("Claude")) === "blocks", "(set-up) Claude attacks with Pet Sehlat and Xindi Surveyors", [r.code, r.error]);
        await reload(); const p1 = await box(cP, false);
        check(p1.bl === "attacker" && p1.y > p0.y + 20, "THEIR attackers step down towards my side", [p0.y, p1.y]);
        check((await badges()).filter(t => t === "⚔ → you").length === 2, "each says it attacks 'you'", await badges());
        r = await seat("Michael", "block", { blocks: { ["#" + cP]: ["#" + ghost], ["#" + cX2]: [] } });
        check(r.code === 200, "(set-up) Michael blocks Pet Sehlat with Ghost of Illyria", [r.code, r.error]);
        if ((await stage("Claude")) === "window") { await reload(); const g1 = await box(ghost), p2 = await box(cP, false), x2 = await box(cX2, false);
          check(g1.bl === "blocker" && g1.y < g0.y - 20, "MY blocker steps up to the line", [g0.y, g1.y]);
          check(Math.abs(g1.x + g1.w / 2 - (p2.x + p2.w / 2)) <= 2, "…and stands opposite the attacker it blocks, wherever it stood in my row", [g1.x, p2.x, g0.x]);
          check(/bl-unb/.test(x2.cls) && p2.y + p2.h <= g1.y + 8, "their unblocked attacker is marked; the two sides do not cover each other", [x2.cls, p2.y + p2.h, g1.y]);
        } else console.log("    (the window closed at once: the table dealt the damage before the page could be looked at; the mirrored blocker position was NOT checked)");
        for (let i = 0; i < 12 && (await stage("Claude")) !== "done"; i++) { await seat("Michael", "pass", {}); await seat("Claude", "next"); await settle(); await sleep(250); }
        await reload(); check((await page.locator("#mat .mc.bl").count()) === 0, "after that combat nobody is on the line");
      }
      check(errors.length === 0, "no script error on the page", errors.slice(0, 3));
      await ctx.close();
    }
  } catch (e) { check(false, "the test ran", e.stack || e); }
  finally { if (browser) await browser.close(); srv.kill(); try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} }
  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join(" | ")}` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
