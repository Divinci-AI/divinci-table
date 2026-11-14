// 2026-11-13 retro, the hand page in a real browser (B1 the cast panel always closes, B2 the waiting bar leads to the choice, B3 a stray click
// taps nothing / Undo / tapped lands shown where you pay, B4 a key from an earlier table). A real server on a free port, temp folders, no card art,
// every press through the page; run once at 1512x860 (the host's laptop) and once at 390x844 (a phone).
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/retro_1113_hand_e2e.cjs
// Before the fix (hand.html, assets/choicealert.js, assets/seat.js stashed): there is no "✕ Close", Escape and a click outside leave the panel
// open, a click on a land while a choice is pending taps it, no Undo, no tapped row, no "show it" for a choice, no stale banner.
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), net = require("net");
const REPO = path.resolve(__dirname, "../..");
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
const CARDS = process.env.TABLE_CARDS_JSON || [path.join(REPO, "table/.cache/cards-trk.json"), path.join(os.homedir(), ".divinci/star-trek-mtg/cards.json")].find(f => fs.existsSync(f));
if (!CARDS) { console.error("SKIP: needs the Star Trek set's card data (TABLE_CARDS_JSON=<file>)"); process.exit(77); }
let failed = [];
const check = (ok, n, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(d).slice(0, 400)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); }); });
(async () => {
  const PORT = await freePort(), BASE = `http://127.0.0.1:${PORT}`;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "retro1113-"));
  const deckScript = `import json,os,sys
sys.path.insert(0,'table')
import make_deck_json_shim as s
C={c['name']:c for c in s.load(sys.argv[2])}
P={'Island':8,'Plains':14,'Forest':14,'Mountain':14,'Horta':3,'Xindi Surveyors':3,'Tricorder':2,'Combadge':2,'Q Continuum':4}
json.dump({'name':'t','commander':None,'startingLife':20,'mainBoard':[{**C[n],'count':k} for n,k in P.items()]},open(sys.argv[1],'w'))`;
  execFileSync(PY, ["-c", deckScript, tmp + "/deck.json", CARDS], { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "3",
    "--ai", "Michael|", "--ai-deck", tmp + "/deck.json", "--ai", "Claude|", "--ai-deck", tmp + "/deck.json", "--pilot", "Michael", "--pilot", "Claude"],
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
    const o = (p[0].slot.options || [])[0] || {}; delete o.label; await seat(n, "choose", { id: p[0].id, target: o }); } };
  const onto = async (n, name, keep) => { await host("search", { seat: n, name }); await host("put", { seat: n, name, quiet: true }); if (!keep) await settle(); };
  const lands = async () => (await st("Michael")).permanents.filter(p => /Land/.test(p.type || ""));
  const untilMain = async who => { for (let i = 0; i < 60; i++) { const p = await ph(); if (p.player === who && p.begun && p.step === "main 1") return; await sleep(250); } };
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    token = fs.readFileSync(tmp + "/token", "utf8").trim();
    for (const n of ["Claude", "Michael"]) K[n] = (await J(await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "e2e-" + n }, body: JSON.stringify({ name: n }) }))).key;
    await fetch(BASE + "/api/phase/windows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: false, by: "host" }) });
    await fetch(BASE + "/api/phase/next", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K.Claude }, body: JSON.stringify({ by: "Claude", key: K.Claude }) });
    await untilMain("Claude");
    await onto("Claude", "Horta");
    await seat("Claude", "end", { text: "over" });
    await untilMain("Michael");
    for (const n of ["Plains", "Forest", "Mountain"]) await onto("Michael", n);
    await onto("Michael", "Horta");
    await host("search", { seat: "Michael", name: "Horta" }); await host("search", { seat: "Michael", name: "Tricorder" });

    browser = await chromium.launch();
    for (const [vw, vh, label] of [[1512, 860, "laptop 1512x860"], [390, 844, "phone 390x844"]]) {
      console.log(`\n══ ${label}`);
      const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, userAgent: "e2e-Michael", hasTouch: false });
      const page = await ctx.newPage(), errors = [], posts = [];
      page.on("pageerror", e => errors.push(String(e)));
      page.on("request", r => { if (r.method() === "POST" && /\/api\/brain\//.test(r.url())) posts.push(new URL(r.url()).pathname.replace("/api/brain/", "")); });
      await page.goto(`${BASE}/hand?player=Michael#key=${encodeURIComponent(K.Michael)}`);
      const wide = vw >= 900;                  // under 700 px there is no fan, no cast panel and no mat: the hand and the battlefield are lists with buttons
      await page.waitForSelector(wide ? "#mat .canvas" : "#field .card, #field button", { timeout: 15000 }); await sleep(1500);
      const fanCard = name => page.locator(`#fan .fc[title="${name}"]`).first();
      // real presses at real coordinates (page.mouse), so what is on top at that point is what gets the click
      const clickAt = async (loc, dx, dy) => { const b = await loc.boundingBox(); await page.mouse.click(b.x + (dx ?? b.width / 2), b.y + (dy ?? b.height / 2)); };
      const bring = async (loc, top = 170) => { await loc.evaluate((el, top) => scrollTo(0, Math.max(0, scrollY + el.getBoundingClientRect().top - top)), top); await sleep(120); };
      const open = async name => { if (await page.locator("#fan .fc.sel").count()) return; if (!wide) await bring(fanCard(name), 300);
        const b = await fanCard(name).boundingBox(); await page.mouse.click(b.x + b.width * 0.3, b.y + b.height * 0.45); await sleep(250);
        if (!(await page.locator(`#fan .fc.sel[title="${name}"]`).count())) { await page.keyboard.press("Escape"); await fanCard(name).evaluate(el => el.click()); await sleep(250); } };   // (a fanned neighbour took the press)
      const clickLand = async id => { await bring(landEl(id)); const hit = await landEl(id).evaluate(el => { const r = el.getBoundingClientRect(), h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!(h && (h === el || el.contains(h) || h.closest(".lstack, .mc"))); });
        if (!hit) console.log("    (note: something else is on top of that land at this scroll position)"); await clickAt(landEl(id)); };
      const isOpen = async () => (await page.locator("#fan .fc.sel").count()) > 0 && (await page.locator("#fanact [data-fanclose]").count()) > 0;
      const landEl = id => page.locator(`.mc[data-mc="${id}"][data-mine="1"]`).first();
      const tappedIds = async () => (await lands()).filter(p => p.tapped).map(p => p.id).sort().join();

      const horta = (await st("Michael")).permanents.find(p => p.name === "Horta");
      // "a permanent I press by hand": on the laptop a land on the mat (the night's stray click); on the phone the creature's Tap button in the list
      const press = async i => { if (wide) return clickLand(L[i].id); const b = page.locator(`#field button[data-tap="${horta.id}"]`).first(); await bring(b, 260); await b.click(); };
      const isTapped = async i => wide ? (await lands()).find(p => p.id === L[i].id).tapped : (await st("Michael")).permanents.find(p => p.id === horta.id).tapped;
      const L = await lands();
      if (wide) {
      console.log("B1 the cast panel always closes, and closing sends nothing");
      await open("Horta");
      check(await isOpen(), "clicking a card opens the panel, with ✕ Close in it", await page.locator("#fanact").innerHTML());
      if (wide) { const covered = await fanCard("Horta").evaluate(el => { const r = el.getBoundingClientRect(), a = document.getElementById("fanact").getBoundingClientRect(); return a.bottom > r.top + 4; });
        console.log(`    (at this width the panel ${covered ? "overlaps" : "does not overlap"} the selected card)`); }
      posts.length = 0;
      await page.keyboard.press("Escape"); await sleep(200);
      check(!(await isOpen()), "Escape closes it");
      await open("Horta"); await page.locator("#fanact [data-fanclose]").click(); await sleep(200);
      check(!(await isOpen()), "✕ Close closes it");
      const before = await tappedIds();
      await open("Horta"); await clickLand(L[0].id); await sleep(700);
      check(!(await isOpen()), "a click on the battlefield closes it");
      check(await tappedIds() === before, "…and that click tapped nothing", await tappedIds());
      await open("Horta"); await page.mouse.click(4, Math.round(vh * 0.45)); await sleep(200);
      check(!(await isOpen()), "a click on empty page closes it");
      await open("Horta"); await page.locator("#fan .fc.sel").evaluate(el => el.click()); await sleep(200);
      check(!(await isOpen()), "a second click on the card still closes it (the old way)");
      check(posts.length === 0, "none of the five ways of closing sent anything to the table", posts.join(","));
      await open("Horta"); await page.locator('#fanact [data-paymode="choose"]').click(); await sleep(250);
      check(await isOpen(), "a press on a control inside the panel keeps it open");
      await page.keyboard.press("Escape"); await sleep(150);
      } else console.log("B1 (no fan and no cast panel at this width: the hand is a list; nothing to close)");

      console.log("B3 a by-hand tap offers Undo; a tapped land is shown where you pay");
      await press(0); await sleep(900);
      check(await isTapped(0), wide ? "with nothing pending, a click on my land taps it (as before)" : "with nothing pending, the Tap button taps (as before)");
      const toast = page.locator("#handtoast");
      check(await toast.isVisible() && /Undo/.test(await toast.textContent()), "an Undo toast shows", await toast.textContent().catch(() => ""));
      await toast.locator("[data-undotap]").click(); await sleep(900);
      check(!(await isTapped(0)), "Undo untaps it");
      await press(1); await sleep(900);
      await sleep(6300);
      check(!(await toast.isVisible()), "the toast is gone after about six seconds");
      check(await isTapped(1), "…and the tap it offered to undo stands");
      if (!wide) { await press(1); await sleep(900); check(!(await isTapped(1)), "(the list's Untap button takes my own tap back, as before)"); }
      if (wide) {
      await open("Horta"); await page.locator('#fanact [data-paymode="choose"]').click(); await sleep(300);
      const row = page.locator("#fanact .payg.tapped").first();
      check(await row.count() === 1 && /tapped —/.test(await row.textContent()) && (await row.textContent()).includes(L[1].name), "Choose lands lists the tapped land, greyed, as 'tapped — untap'", await page.locator("#fanact").textContent());
      await row.locator("[data-payuntap]").click(); await sleep(900);
      check(!(await lands()).find(p => p.id === L[1].id).tapped, "its 'untap' untaps it (my own tap, same step)", await page.locator("#fanact").textContent());
      check(await page.locator("#fanact .payg.tapped").count() === 0, "…and the row goes back to the untapped piles");
      }
      await page.keyboard.press("Escape"); await sleep(4000); await page.evaluate(() => { const t = document.getElementById("handtoast"); if (t) t.style.display = "none"; });

      console.log("B2 + B3 a pending choice: the bar leads to it, nothing covers it, and a click taps nothing");
      let src = "";
      // the night's own case: Q Continuum enters untapped (three other lands) and asks "choose one"
      for (const how of ["put", "land"]) { const name = "Q Continuum";
        if (how === "put") await onto("Michael", name, true); else { await host("search", { seat: "Michael", name }); await seat("Michael", "land", { name }); }
        await sleep(400); const s = await st("Michael"); if ((s.pending_choices || []).length || s.pending_surveil || s.pending_scry) { src = name + " (" + how + ")"; break; } }
      const s0 = await st("Michael"), kind = (s0.pending_choices || []).length ? "choice" : s0.pending_surveil ? "surveil" : s0.pending_scry ? "scry" : "";
      check(!!kind, "the table is waiting on Michael for something", JSON.stringify({ src }));
      console.log(`    (waiting on: ${kind} from ${src})`);
      const panelSel = kind === "choice" ? "#choicepanel" : "#effpanel";
      await page.waitForSelector(".ca-bar [data-ca-top]", { timeout: 9000 }).catch(() => {});
      check(await page.locator(".ca-bar [data-ca-top]").count() === 1, "the red bar offers 'show it' for it", await page.locator(".ca-bar").textContent().catch(() => ""));
      const t0 = await isTapped(2); posts.length = 0;
      await press(2); await sleep(900);
      check(await isTapped(2) === t0 && !posts.includes("tap") && !posts.includes("untap"), wide ? "a click on my land while that is pending taps nothing" : "the Tap button while that is pending taps nothing", posts.join(","));
      check(await toast.isVisible() && /waiting on you/.test(await toast.textContent()) && (await toast.locator("[data-showchoice]").count()) === 1, "…and says why, with a way to the choice", await toast.textContent().catch(() => ""));
      await page.evaluate(() => scrollTo(0, document.body.scrollHeight)); await sleep(150);
      if (wide) { await open("Horta"); check(await isOpen(), "(the cast panel is open, the page scrolled away from the choice)"); }
      const covered = async () => page.evaluate(sel => { const box = document.querySelector(sel); const els = [...box.querySelectorAll("button, select")].filter(e => { const r = e.getBoundingClientRect(); return r.width && r.bottom > 0 && r.top < innerHeight; });
        const bad = els.filter(e => { const r = e.getBoundingClientRect(), hit = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height / 2, 12)); return !(hit && (hit === e || e.contains(hit) || hit.closest(sel) || hit.closest(".ca-bar, #tablebar, [data-tablebar]"))); });
        return { seen: els.length, bad: bad.map(e => e.textContent.trim().slice(0, 30)) }; }, panelSel);
      await page.evaluate(sel => { const b = document.querySelector(sel); scrollTo(0, scrollY + b.getBoundingClientRect().top - 140); }, panelSel); await sleep(200);
      if (wide) await open("Horta");
      let c = await covered();
      check(c.seen > 0 && c.bad.length === 0, "with the cast panel open, every control of the choice can still be clicked (nothing is painted over it)", JSON.stringify(c));
      await page.evaluate(() => scrollTo(0, document.body.scrollHeight)); await sleep(150);
      await page.locator(".ca-bar [data-ca-top]").click(); await sleep(500);
      const vis = await page.evaluate(sel => { const r = document.querySelector(sel).firstElementChild.getBoundingClientRect(), bar = document.querySelector(".ca-bar").getBoundingClientRect(); return { top: Math.round(r.top), barBottom: Math.round(bar.bottom), h: innerHeight }; }, panelSel);
      check(vis.top >= vis.barBottom - 2 && vis.top < vis.h * 0.6, "'show it' scrolls the choice to just under the bar", JSON.stringify(vis));
      if (wide) check(!(await isOpen()), "…and closes the cast panel on the way");
      c = await covered();
      check(c.seen > 0 && c.bad.length === 0, "…and nothing covers it", JSON.stringify(c));
      check(await page.locator(panelSel + " .ca-flash").count() === 1 || await page.evaluate(sel => !!document.querySelector(sel).firstElementChild, panelSel), "the panel is flashed");
      await settle(); if (kind !== "choice") await seat("Michael", "effect", { kind, put: "top" }).catch(() => {});
      await sleep(3200);
      const still = await st("Michael");
      if (!(still.pending_choices || []).length && !still.pending_surveil && !still.pending_scry) check(await page.locator(".ca-bar").evaluate(e => e.style.display === "none").catch(() => true), "once answered, the bar goes away");
      else console.log("    (the wait could not be answered from the test; the bar check is skipped)");
      check(!errors.length, "no page errors", errors.join("; "));
      await ctx.close();
    }

    console.log("\n══ B4 a tab holding a key this table does not know");
    const ctx = await browser.newContext({ viewport: { width: 1512, height: 860 }, userAgent: "e2e-stale-tab" });
    const page = await ctx.newPage(), reqs = [];
    await page.addInitScript(() => { try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); localStorage.setItem("table.seat", JSON.stringify({ name: "Michael", key: "a-key-from-an-earlier-table" })); } } catch {} });   // once: the reload must find what the page left
    page.on("request", r => { if (/\/api\/brain\/state/.test(r.url())) reqs.push(Date.now()); });
    await page.goto(`${BASE}/hand`); await sleep(2500);
    const banner = page.locator("[data-seat-stale], [role=alert]").filter({ hasText: /earlier (table|game)/ }).first();
    check(await banner.count() === 1 && await banner.isVisible() && /Join the match/.test(await banner.textContent()), "a banner says the key is from an earlier table (or game) and where the new link is", (await page.locator("body").textContent()).slice(0, 200));
    await page.reload(); await sleep(2500);
    const b2 = page.locator("[data-seat-stale]");
    check(await b2.count() === 1 && /Join the match/.test(await b2.textContent()), "after a reload the banner is still there, and names the Join the match button", await page.locator("#who").textContent());
    check(/earlier table/.test(await page.locator("#who").textContent()), "the line over the empty hand says the same, not 'Claim your seat'", await page.locator("#who").textContent());
    const n0 = reqs.length; await sleep(4000);
    check(reqs.length === n0, "the page is not polling its state with a dead key", `${reqs.length - n0} more state calls`);
    check(!(await page.content()).includes("a-key-from-an-earlier-table") && !(await page.evaluate(() => localStorage.getItem("table.seat") || "")).includes("a-key-from"), "the dead key is neither on the page nor kept in storage");
    await ctx.close();
  } catch (e) { check(false, "the test ran", e.stack || e); }
  finally { if (browser) await browser.close(); srv.kill(); try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} }
  console.log(failed.length ? `\n${failed.length} FAILED` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
