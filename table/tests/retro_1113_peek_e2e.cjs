// 2026-11-13, items 6 and 7, in a real browser: the whole card beside an option of a "your choice" panel, and mana symbols drawn as pictures.
// A real server on a free port, temp folders. The table's art folder is one this test makes: ONE card (Horta) has "art", a blank 1x1 picture
// written here (no card art is used or copied); every other card has none, so both faces of the preview are seen.
//   laptop 1512x860, a real mouse and keyboard;  phone 390x844, real touches (CDP Input.dispatchTouchEvent).
// The prompt is the one from the table: Stray from History, "Target opponent reveals their hand. You choose a nonland card from it."
// Not here: a real phone, Safari / WebKit, Firefox, a screen reader, the surveil / scry rows (marked the same way, not pressed here).
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/retro_1113_peek_e2e.cjs
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
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "retro1113pk-"));
  const deckScript = `import json,os,sys
sys.path.insert(0,'table')
import make_deck_json_shim as s
C={c['name']:c for c in s.load(sys.argv[2])}
P={'Swamp':20,'Forest':14,'Mountain':14,'Horta':3,'Moopsy':4,'Plasma Cascade':4,'Stray from History':4,'Xindi Surveyors':3}
json.dump({'name':'t','commander':None,'startingLife':20,'mainBoard':[{**C[n],'count':k} for n,k in P.items()]},open(sys.argv[1],'w'))`;
  execFileSync(PY, ["-c", deckScript, tmp + "/deck.json", CARDS], { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  fs.mkdirSync(tmp + "/art"); fs.writeFileSync(tmp + "/art/index.js", 'window.CARD_ART = {"Horta": "h1"};');
  fs.writeFileSync(tmp + "/art/h1.jpg", Buffer.from("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAA//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AN//Z", "base64"));
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "3",
    "--ai", "Michael|", "--ai-deck", tmp + "/deck.json", "--ai", "Claude|", "--ai-deck", tmp + "/deck.json", "--pilot", "Michael", "--pilot", "Claude", "--seat-rate", "0"],
    { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", TABLE_NO_VOICE: "1", TABLE_HIGHROLL: "first", TABLE_CHECKIN: "off", STRICT_SEATS: "1", HF_HUB_OFFLINE: "1", ROUTER: "code",
      REPLIES: "template", NO_GEMMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TABLE_CARDS_JSON: CARDS, TABLE_ART_DIR: tmp + "/art", TYPESAFE_API_KEY: "" }, stdio: "ignore" });
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
    const cXindi = await onto("Claude", "Xindi Surveyors");
    await seat("Claude", "end", { text: "over" });
    await untilMain("Michael");
    for (const n of ["Swamp", "Swamp", "Swamp", "Mountain", "Mountain", "Mountain"]) await onto("Michael", n);
    const give = async (n, name) => host("search", { seat: n, name });
    const claudeHas = async name => { const b = await J(await fetch(`${BASE}/api/board3d`)); return (b.seats.find(s => s.name === "Claude") || {}).hand; };
    for (const n of ["Horta", "Moopsy", "Plasma Cascade", "Forest", "Moopsy"]) await give("Claude", n);
    const legal = async n => J(await fetch(`${BASE}/api/seat/legal?seat=${n}`, { headers: { "X-Seat-Key": K[n] } }));
    const castStray = async () => { await give("Michael", "Stray from History"); const acts = ((await legal("Michael")).actions || []).filter(a => a.action === "cast" && a.body.name === "Stray from History");
      const a = acts.find(x => /Repair|reveals|hand/i.test(x.why || "") || JSON.stringify(x.body).includes('"player"')) || acts[acts.length - 1];
      if (!a) return { code: 0, error: "not in the legal list: " + JSON.stringify(acts) }; const { seat: _s, ...b } = a.body; return seat("Michael", "cast", b); };
    const pending = async () => ((await st("Michael")).pending_choices || [])[0];

    browser = await chromium.launch();
    for (const [vw, vh, label, touch] of [[1512, 860, "laptop 1512x860, mouse and keyboard", false], [390, 844, "phone 390x844, real touches", true]]) {
      console.log(`\n══ ${label}`);
      await newTurn();
      let r = await castStray(), pc = await pending();
      check(r.code === 200 && pc && (pc.slot.options || []).length >= 3, "(set-up) Stray from History is cast at Claude; the table asks Michael which nonland card to exile", [r.code, r.error, pc && pc.slot]);
      const labels = ((pc && pc.slot.options) || []).map(o => o.label);
      const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, userAgent: "e2e-Michael", hasTouch: touch, isMobile: touch });
      const page = await ctx.newPage(), errors = [], posts = [], looked = [];
      page.on("pageerror", e => errors.push(String(e)));
      page.on("request", q => { if (q.method() === "POST" && /\/api\/brain\//.test(q.url())) posts.push(new URL(q.url()).pathname.replace("/api/brain/", "")); if (/\/api\/card\?/.test(q.url())) looked.push(new URL(q.url()).searchParams.get("name")); });
      await page.goto(`${BASE}/hand?player=Michael#key=${encodeURIComponent(K.Michael)}`);
      await page.waitForSelector("#choicepanel [data-choose]", { timeout: 15000 }); await sleep(1500);
      const cdp = touch ? await ctx.newCDPSession(page) : null;
      const T = async (type, x, y) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x: Math.round(x), y: Math.round(y), id: 1 }] });
      const opt = name => page.locator(`#choicepanel button[data-choose][data-peek="${name}"]`).first();
      const view = () => page.evaluate(() => { const b = document.getElementById("artbox"), f = document.getElementById("artface"), im = b.firstChild, r = b.getBoundingClientRect(), cs = getComputedStyle(b);
        const shown = cs.display !== "none"; return { shown, peek: b.classList.contains("peek"), pe: cs.pointerEvents, rect: [r.left, r.top, r.right, r.bottom], side: b.dataset.side,
          face: getComputedStyle(f).display !== "none" ? f.textContent : "", faceCard: f.dataset.card || "", img: getComputedStyle(im).display !== "none" ? im.getAttribute("src") || "" : "",
          cost: (f.querySelector(".fn span:last-child") || {}).textContent || "", ms: f.querySelectorAll(".ms").length }; });
      const apart = async (name, v) => { const b = await opt(name).boundingBox(); const [l, t, rr, bb] = v.rect; return l >= b.x + b.width || rr <= b.x || t >= b.y + b.height || bb <= b.y; };
      const onTop = name => opt(name).evaluate(el => { const r = el.getBoundingClientRect(), h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return h === el || el.contains(h); });
      const bring = async (loc, top = 260) => { await loc.evaluate((el, top) => scrollTo(0, Math.max(0, scrollY + el.getBoundingClientRect().top - top)), top); await sleep(200); };
      const chosen = () => posts.filter(p => p === "choose").length;

      console.log("the prompt");
      const marked = await page.$$eval("#choicepanel button[data-choose]", els => els.map(e => [e.dataset.peek || "", e.textContent]));
      check(["Horta", "Moopsy", "Plasma Cascade"].every(n => marked.some(m => m[0] === n)) && marked.every(m => m[0]), "every option of the panel is marked with the card it names (Horta, Moopsy, Plasma Cascade)", marked);
      check(marked.every(m => labels.includes(m[1])), "each option's text is still exactly the table's label, braces and all ('Horta {4}{R}{R} (Creature 7/6)')", [marked.map(m => m[1]), labels]);
      const ms = await opt("Horta").evaluate(el => [...el.querySelectorAll(".ms")].map(s => [s.getAttribute("aria-label"), s.getAttribute("role"), s.querySelector("svg") ? 1 : 0, Math.round(s.getBoundingClientRect().width)]));
      check(ms.map(m => m[0]).join("") === "{4}{R}{R}" && ms.every(m => m[1] === "img" && m[2] === 1 && m[3] >= 8), "its cost is drawn as three small pictures, each named by its token for a screen reader ({4}, {R}, {R})", ms);
      check(await page.locator("#choicepanel .peekbtn").count() === marked.length, "each option has its own small 'view' button beside it");
      check(!marked.some(m => /Forest/.test(m[1])) && !looked.includes("Forest"), "the land in that hand is not an option, and the page has asked the card lookup nothing about it", looked);

      if (!touch) {
        console.log("hover");
        await bring(opt("Moopsy")); posts.length = 0;
        await opt("Moopsy").hover(); await sleep(500);
        let v = await view();
        check(v.shown && v.peek, "the mouse over 'Moopsy …' shows the whole card", v);
        check(v.faceCard === "Moopsy" && ["Moopsy", "Creature — Beast", "Deathtouch", "This creature gains reach until end of turn.", "1/1"].every(x => v.face.includes(x)) && !v.img,
          "no art for Moopsy at this table: a text card face with its name, type line, full rules text and power/toughness", v.face);
        check(v.cost === "{G}" && v.ms >= 2, "…its cost and the {2} in its text drawn as symbols, and still the text '{G}'", [v.cost, v.ms]);
        check(await apart("Moopsy", v) && v.rect[0] >= 0 && v.rect[2] <= vw + 0.5 && v.rect[1] >= 0 && v.rect[3] <= vh + 0.5, "the preview does not cover the option and is inside the screen (" + v.side + " of it)", v.rect);
        check(v.pe === "none" && await onTop("Moopsy"), "the option under the mouse is still the thing a click would land on (the preview takes no clicks)");
        check(chosen() === 0 && posts.length === 0, "hovering sends nothing: no choice is made", posts);
        await opt("Horta").hover(); await sleep(400); v = await view();
        check(v.shown && v.peek && v.img === "/art/h1.jpg" && !v.face && await apart("Horta", v), "the mouse over 'Horta …' (the one card with art here): the art is shown instead, beside the option", v);
        await page.mouse.move(vw - 5, vh - 5); await sleep(300);
        check(!(await view()).shown, "moving the mouse away puts the preview away");
        console.log("keyboard");
        await opt("Plasma Cascade").focus(); await sleep(450); v = await view();
        check(v.shown && v.peek && v.faceCard === "Plasma Cascade" && /deals 3 damage to any target/.test(v.face) && /Instant/.test(v.face) && await apart("Plasma Cascade", v), "keyboard focus on an option shows its card too", v.face);
        await page.keyboard.press("Escape"); await sleep(200);
        check(!(await view()).shown && chosen() === 0, "Escape puts it away; nothing was chosen");
        console.log("the view button");
        await opt("Moopsy").locator("xpath=following-sibling::button[contains(@class,'peekbtn')]").click(); await sleep(400); v = await view();
        check(v.shown && !v.peek && v.faceCard === "Moopsy" && /Deathtouch/.test(v.face) && chosen() === 0, "'view' opens the card large (the page's own enlarged-card box) and chooses nothing", v);
        await page.mouse.click(20, 20); await sleep(250);
        check(!(await view()).shown && chosen() === 0 && !!(await pending()), "a click closes it; the question is still open");
        console.log("a target dropdown");
        const sel = page.locator("#fanact select[data-peeksel], #hand select[data-peeksel]").first();
        await give("Michael", "Plasma Cascade"); await page.evaluate(() => load()); await sleep(600);
        const fc = page.locator('#fan .fc[title="Plasma Cascade"]').first();
        if (await fc.count()) { await fc.evaluate(el => el.click()); await sleep(400); }
        const names = await page.$$eval("#fanact select[data-peeksel] option", os => os.map(o => [o.textContent, o.dataset.peek || ""]));
        check(names.some(n => n[1] === "Xindi Surveyors") && names.some(n => /Claude$|Michael$|you/i.test(n[0]) ? n[1] === "" : true), "a spell's target dropdown marks each creature option with its card (a player option names none)", names);
        if (names.length) { await page.locator("#fanact select[data-peeksel]").first().selectOption({ index: Math.max(0, names.findIndex(n => n[1] === "Xindi Surveyors")) }); await page.locator("#fanact select[data-peeksel]").first().hover(); await sleep(450); v = await view();
          check(v.shown && v.peek && v.faceCard === "Xindi Surveyors" && /Landfall/.test(v.face), "the mouse over the dropdown shows the card it currently has selected", v);
          const sb = await page.locator("#fanact select[data-peeksel]").first().boundingBox(); const [l, t, rr, bb] = v.rect;
          check(l >= sb.x + sb.width || rr <= sb.x || t >= sb.y + sb.height || bb <= sb.y, "…beside the dropdown, not over it"); }
        await page.mouse.move(vw - 5, vh - 5); await page.keyboard.press("Escape"); await sleep(300);
      } else {
        console.log("press and hold");
        await bring(opt("Moopsy"), 300); posts.length = 0;
        let b = await opt("Moopsy").boundingBox();
        await T("touchStart", b.x + 40, b.y + b.height / 2); await sleep(750);
        let v = await view();
        check(v.shown && v.peek && v.faceCard === "Moopsy" && /Deathtouch/.test(v.face), "a finger held on 'Moopsy …' for half a second shows the whole card", v);
        check(await apart("Moopsy", v) && v.rect[0] >= 0 && v.rect[2] <= vw + 0.5, "it does not cover the option, and is inside the screen (" + v.side + ")", v.rect);
        await T("touchEnd"); await sleep(600);
        check(chosen() === 0 && !!(await pending()), "letting go CHOOSES NOTHING: the hold was only a look", posts);
        check((await view()).shown, "the card stays up after the finger lifts, to be read");
        await bring(page.locator("#choicepanel h2"), 320);              // (well clear of the fixed bar at the top: a tap there is a tap on its tabs)
        const h = await page.locator("#choicepanel h2").boundingBox(); await T("touchStart", h.x + 20, h.y + 6); await sleep(40); await T("touchEnd"); await sleep(400);
        check(!(await view()).shown && chosen() === 0, "the next tap anywhere puts it away, and chooses nothing");
        console.log("the view button");
        const vb = opt("Plasma Cascade").locator("xpath=following-sibling::button[contains(@class,'peekbtn')]"); await bring(vb, 300); b = await vb.boundingBox();
        check(b.width >= 36 && b.height >= 34, "the 'view' button is big enough for a thumb", b);
        await T("touchStart", b.x + b.width / 2, b.y + b.height / 2); await sleep(40); await T("touchEnd"); await sleep(500); v = await view();
        check(v.shown && !v.peek && v.faceCard === "Plasma Cascade" && /deals 3 damage/.test(v.face) && chosen() === 0, "a tap on 'view' opens the card large and chooses nothing", v);
        await T("touchStart", vw / 2, vh / 2); await sleep(40); await T("touchEnd"); await sleep(400);
        check(!(await view()).shown && chosen() === 0 && !!(await pending()), "a tap closes it; the question is still open");
      }

      console.log("privacy");
      const listed = new Set(marked.map(m => m[0]));
      check(looked.every(n => listed.has(n) || n === "Xindi Surveyors"), `the page looked up ${looked.length ? "only cards the prompt (or the public battlefield) names" : "no card at all: everything it showed was in what this seat had already been sent"}`, looked);
      const c1 = await J(await fetch(`${BASE}/api/card?name=${encodeURIComponent("Forest")}`)), c2 = await J(await fetch(`${BASE}/api/card?name=${encodeURIComponent("Sickbay")}`));
      // (integration 2026-11-14: the lookup now also says where its answer came from. For a keyless page that is a static source, oracle or
      // set, never "deck" or "public": so it says nothing about where a copy of the card is.)
      check(Object.keys(c1).sort().join() === "cost,name,power,source,text,toughness,type" && Object.keys(c2).sort().join() === Object.keys(c1).sort().join()
        && ["oracle", "set"].includes(c1.source) && ["oracle", "set"].includes(c2.source),
        "the lookup answers the same way for a card that IS in the hidden hand (Forest) and one that is in no zone at all (Sickbay): printed text only, nothing about where a card is", [Object.keys(c1), Object.keys(c2)]);

      console.log("choosing still works");
      posts.length = 0; await bring(opt("Moopsy"), 300);
      if (touch) { const b = await opt("Moopsy").boundingBox(); await T("touchStart", b.x + 40, b.y + b.height / 2); await sleep(40); await T("touchEnd"); } else await opt("Moopsy").click();
      await sleep(1200);
      const ex = ((await J(await fetch(`${BASE}/api/board3d`))).seats.find(s => s.name === "Claude") || {}).exile || [];
      check(chosen() === 1 && !(await pending()) && ex.includes("Moopsy"), "a plain " + (touch ? "tap" : "click") + " on the option chooses it, once: Moopsy is exiled", [posts, ex]);
      check(!(await view()).shown || (await view()).peek === false, "no preview is left behind");
      check(errors.length === 0, "no script error on the page", errors.slice(0, 3));
      await ctx.close();
    }

    console.log("\n══ mana symbols on /board and /watch (1280x1080)");
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 1080 } });
    for (const [url, what] of [["/board", "/board"], ["/watch", "/watch (the delayed page)"]]) {
      const page = await ctx.newPage(), errors = []; page.on("pageerror", e => errors.push(String(e)));
      const reqs = []; page.on("request", q => reqs.push(new URL(q.url()).pathname));
      await page.goto(BASE + url); await sleep(3500);
      const has = await page.evaluate(() => !!(window.ManaSym && window.CardArt));
      check(has, `${what}: the symbol helper is loaded`);
      const card = page.locator(".seat .card").filter({ hasText: "Xindi Surveyors" }).first();
      if (await card.count()) { await card.hover(); await sleep(400);
        const tip = await page.evaluate(() => { const t = document.getElementById("tip"); return { text: t.textContent, ms: [...t.querySelectorAll(".ms")].map(s => s.getAttribute("aria-label")), shown: getComputedStyle(t).display !== "none" }; });
        check(tip.shown && tip.ms.join("") === "{2}{G}" && tip.text.includes("{2}{G}"), `${what}: a card's cost in its tooltip is drawn as symbols and is still the text '{2}{G}'`, tip);
      } else check(url === "/watch", `${what}: no card on the page to hover (on /watch that is the delay: the snapshot shown is of an earlier turn)`, await page.locator("body").textContent());
      if (url === "/watch") check(!reqs.some(p => /^\/api\/(board3d|phase|brain|events)/.test(p)) && reqs.includes("/api/spectate"), "/watch still reads only the delayed feed (/api/spectate): allowing mana.js through added no live request", [...new Set(reqs)].filter(p => p.startsWith("/api")));
      check(errors.length === 0, `${what}: no script error`, errors.slice(0, 3));
      await page.close();
    }
    await ctx.close();
  } catch (e) { check(false, "the test ran", e.stack || e); }
  finally { if (browser) await browser.close(); srv.kill(); try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} }
  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join(" | ")}` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
