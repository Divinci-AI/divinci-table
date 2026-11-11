// v6d: one whole game flow in a real (headless) browser, on a desktop viewport and on a 390px phone viewport:
//   opening hand (keep) -> attack (Confirm attack) -> ONE block panel -> "no block" -> the window after blocks with a flash spell that a counterspell HOLDS
//   -> the held-spell prompt -> the one priority chip -> combat damage -> the game-over banner.
// A real server on a free port (never 8800/8801/8820/8830), its own research folder, seats TestA and TestB, fixture-free deck built from the card data,
// nothing read from live/. One browser, one page per seat (headless: nothing plays sound).
//   PW=.../@playwright/test VIEW=desktop|phone SHOTS=dir node table/tests/combat_flow_e2e.cjs
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), net = require("net");
const TABLE = path.resolve(__dirname, "..");
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
const VIEW = process.env.VIEW || "desktop", VP = VIEW === "phone" ? { width: 390, height: 844 } : { width: 1280, height: 900 };
const SHOTS = process.env.SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), "flow-shots-"));
fs.mkdirSync(SHOTS, { recursive: true });
const LIVE = [8800, 8801, 8820, 8830];
let failed = [];
const check = (ok, n, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} [${VIEW}] ${n}${ok ? "" : " — " + String(typeof d === "string" ? d : JSON.stringify(d)).slice(0, 500)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(LIVE.includes(p) ? freePort() : p)); }); });
(async () => {
  const PORT = await freePort(), BASE = `http://127.0.0.1:${PORT}`, A = "TestA", B = "TestB";
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "combat-flow-"));
  const deckScript = `import json,os,sys
sys.path.insert(0, sys.argv[2])
import make_deck_json_shim as s
C={c['name']:c for c in s.load(os.path.expanduser('~/.divinci/star-trek-mtg/cards.json'))}
P={'Island':16,'Plains':16,'Xindi Surveyors':3,'Starfleet Security':3,'Tal Shiar Operative':3,'Mugato':3,'Saavik, Stoic Student':3,'Consider the Prime Directive':3}
json.dump({'name':'t','commander':None,'startingLife':20,'mainBoard':[{**C[n],'count':k} for n,k in P.items()]},open(sys.argv[1],'w'))`;
  execFileSync(PY, ["-c", deckScript, tmp + "/deck.json", TABLE], { env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const srv = spawn(PY, [path.join(TABLE, "server.py"), "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "5",
    "--ai", `${A}|`, "--ai-deck", tmp + "/deck.json", "--ai", `${B}|`, "--ai-deck", tmp + "/deck.json", "--pilot", A, "--pilot", B, "--order", `${A},${B}`, "--seat-rate", "0"],
    { cwd: path.dirname(TABLE), env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", TABLE_NO_VOICE: "1", TABLE_HIGHROLL: "first", TABLE_CHECKIN: "off", TABLE_MULLIGAN: "on", TABLE_MULLIGAN_SECS: "0",
      STRICT_SEATS: "1", HF_HUB_OFFLINE: "1", ROUTER: "code", REPLIES: "template", NO_GEMMA: "1", SKIP_OLLAMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TYPESAFE_API_KEY: "" },
      stdio: ["ignore", fs.openSync(tmp + "/log", "w"), fs.openSync(tmp + "/log", "a")] });
  let browser;
  const J = async r => { const t = await r.text(); try { return JSON.parse(t); } catch { return { error: t.slice(0, 200) }; } };
  let token = "", K = {};
  const hdr = n => ({ "Content-Type": "application/json", "X-Seat-Key": K[n], "User-Agent": "e2e-" + n });
  const host = async (a, b) => J(await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Brain-Token": token }, body: JSON.stringify(b) }));
  const seat = async (n, a, b = {}) => { const r = await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: hdr(n), body: JSON.stringify({ seat: n, ...b }) }); return { code: r.status, ...(await J(r)) }; };
  const st = async n => J(await fetch(`${BASE}/api/brain/state?seat=${n}`, { headers: hdr(n) }));
  const ph = async () => J(await fetch(`${BASE}/api/phase`));
  const life = async n => (await J(await fetch(`${BASE}/api/life`)))[n];
  const onto = async (n, name) => { await host("search", { seat: n, name }); await host("put", { seat: n, name, quiet: true }); return (await st(n)).permanents.filter(p => p.name === name).pop().id; };
  const toHand = async (n, name) => host("search", { seat: n, name });
  const emptyHands = async () => { for (const n of [A, B]) for (const h of (await st(n)).hand || []) await host("discard", { seat: n, name: h.name, quiet: true }); };
  const passFor = async n => { const p = await ph(); return seat(n, "pass", { player: p.player, step: p.step }); };
  const untilMain = async who => { for (let i = 0; i < 80; i++) { const p = await ph(); if (p.player === who && p.begun && p.step === "main 1") return; const nx = (p.passes || {}).next; if (K[nx]) await passFor(nx); await sleep(250); } };
  const turnTo = async who => { const p = await ph(); if (p.player && p.begun) await seat(p.player, "end", { text: "over" }); await untilMain(who); await emptyHands(); };
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    token = fs.readFileSync(tmp + "/token", "utf8").trim();
    for (const n of [A, B]) K[n] = (await J(await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "e2e-" + n }, body: JSON.stringify({ name: n }) }))).key;
    await fetch(BASE + "/api/phase/windows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: false, by: "host" }) });
    browser = await chromium.launch();
    const open = async n => { const ctx = await browser.newContext({ viewport: VP, userAgent: "e2e-" + n });
      const page = await ctx.newPage(); page.__errors = []; page.on("pageerror", e => page.__errors.push(String(e)));
      await page.goto(`${BASE}/hand?player=${n}#key=${encodeURIComponent(K[n])}`); await page.waitForSelector("#stepbar", { state: "attached", timeout: 15000 }); await sleep(1200); return page; };
    const pa = await open(A), pb = await open(B);
    const shot = (p, n) => p.screenshot({ path: path.join(SHOTS, `${VIEW}-${n}.png`) });
    const text = async (p, sel) => (await p.locator(sel).first().textContent().catch(() => "")) || "";
    const noHScroll = async p => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
    const visiblePass = p => p.evaluate(() => [...document.querySelectorAll("button")].filter(b => { const r = b.getBoundingClientRect(), cs = getComputedStyle(b), bar = b.closest(".u-bar"); return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && (!bar || document.body.classList.contains("u-on")) && getComputedStyle(b.closest(".u-bar") || b).display !== "none"; }) .filter(b => /^\s*Pass\b/i.test(b.textContent) && !/Pass: let it resolve/.test(b.textContent)).map(b => b.textContent.trim().slice(0, 30)));

    console.log("opening hand");
    await ph(); await fetch(BASE + "/api/phase/next", { method: "POST", headers: hdr(A), body: JSON.stringify({ by: A, key: K[A] }) });
    await sleep(1500); await pa.evaluate(() => window.load && window.load()).catch(() => {}); await pb.evaluate(() => window.load && window.load()).catch(() => {}); await sleep(1200);
    check(await pa.locator("#mullpanel [data-mullkeep]").count() === 1 && await pb.locator("#mullpanel [data-mullkeep]").count() === 1, "both pages show the keep / mulligan prompt", await text(pa, "#mullpanel"));
    check(await noHScroll(pa), "no sideways scroll on the opening-hand page");
    await shot(pa, "1-mulligan");
    await pa.click("#mullpanel [data-mullkeep]"); await pb.click("#mullpanel [data-mullkeep]"); await sleep(1500);
    await untilMain(A); await emptyHands();
    check((await ph()).player === A, "both kept: the first turn starts", (await ph()).mulligan);

    console.log("set-up: TestA 4 power on the board, TestB (at 4 life) holds a counterspell and TestA holds Saavik");
    const xs = await onto(A, "Xindi Surveyors"), ss = await onto(A, "Starfleet Security");
    for (let i = 0; i < 5; i++) await onto(A, "Plains");
    for (let i = 0; i < 3; i++) await onto(B, "Island");
    await onto(B, "Tal Shiar Operative");
    await turnTo(B); await turnTo(A);
    await toHand(A, "Saavik, Stoic Student");                  // (TestB gets its counterspell once the attack is in: a seat that holds an instant is never passed for, so it would have to pass main 1 first)
    await host("life", { seat: B, player: B, delta: -16, quiet: true });
    check(await life(B) === 4, "TestB is at 4", await life(B));
    for (const p of [pa, pb]) await p.evaluate(() => window.load && window.load()).catch(() => {}); await sleep(1500);

    console.log("attack");
    await pa.click("#attackgo"); await sleep(500);
    check(/Confirm attack with 2/.test(await text(pa, "#attack")), "Confirm attack with 2", await text(pa, "#attack"));
    await pa.click("#attack"); await sleep(1500);
    check(((((await ph()).combat || {}).attacks || [])[0] || {}).stage === "blocks", "the declaration is in; TestB is asked", { err: await text(pa, "#err"), phase: (await ph()).step, player: (await ph()).player, combat: (await ph()).combat });
    await toHand(B, "Consider the Prime Directive");
    await pa.evaluate(() => window.load && window.load()).catch(() => {}); await pb.evaluate(() => window.load && window.load()).catch(() => {}); await sleep(1500);

    console.log("the block panel");
    const panel = pb.locator("#blockpanel");
    check(await panel.count() === 1 && /declare your blocks/.test(await panel.textContent()), "ONE block panel for both attackers", await text(pb, "#alert"));
    const pass1 = await visiblePass(pb);
    check(pass1.length === 0, "no Pass button anywhere while blocks are owed (the panel is the one prompt)", pass1);
    check(!(await pb.evaluate(() => document.body.classList.contains("u-on"))), "the priority chip is hidden while blocks are owed");
    check(await noHScroll(pb), "no sideways scroll with the block panel up");
    const box = await panel.boundingBox(); check(!!box && box.x >= 0 && box.x + box.width <= VP.width + 1, "the panel fits the viewport width", box);
    await shot(pb, "2-block-panel");
    await pb.click("#blockpanel [data-take]"); await sleep(400); await pb.click("#blockpanel [data-take]"); await sleep(1500);

    console.log("the window after blocks, with a flash creature and a counterspell");
    let p = await ph();
    check(p.step === "declare blockers" && ((p.combat.attacks || [])[0] || {}).stage === "window" && await life(B) === 4, "blocks are in (none); nothing dealt; the window is open", { step: p.step, c: p.combat });
    await pb.evaluate(() => window.load && window.load()).catch(() => {}); await pa.evaluate(() => window.load && window.load()).catch(() => {}); await sleep(1500);
    const chipVisible = await pb.evaluate(() => document.body.classList.contains("u-on"));
    const pass2 = await visiblePass(pb);
    check(chipVisible && pass2.length === 1, "TestB (who holds a counterspell) sees exactly ONE Pass: the priority chip", { chipVisible, pass2 });
    check(/Blocks are declared/.test(await text(pb, "#alert")) && !/data-pass/.test(await pb.locator("#alert").innerHTML()), "the window note has no second Pass of its own", await text(pb, "#alert"));
    await shot(pb, "3-window-defender");
    const c = await seat(A, "cast", { name: "Saavik, Stoic Student" });
    await sleep(1500); await pb.evaluate(() => window.load && window.load()).catch(() => {}); await pa.evaluate(() => window.load && window.load()).catch(() => {}); await sleep(1500);
    p = await ph();
    check(c.code === 200 && (p.held || {}).spell && (p.held.waiting || []).includes(B), "TestA flashes Saavik in: it is HELD for TestB", { c: c.code, held: p.held });
    const hp = pb.locator("#heldpanel");
    check(await hp.count() === 1 && /casts Saavik/.test(await hp.textContent()) && await pb.locator("[data-heldpass]").count() === 1, "TestB's page shows the held-spell prompt with its own Pass", await text(pb, "#heldbox"));
    const pass3 = await visiblePass(pb);
    check(pass3.length === 0, "…and no second Pass: the chip steps aside for the held-spell prompt", pass3);
    check(/waiting/i.test(await text(pa, "#heldbox")), "TestA's page says it is waiting on TestB", await text(pa, "#heldbox"));
    await shot(pb, "4-held-spell"); await shot(pa, "4-held-spell-caster");
    check(await life(B) === 4 && (await ph()).step === "declare blockers", "while the spell is held nothing is dealt", await life(B));
    await pb.click("[data-heldpass]"); await sleep(1800);
    check(!!(await st(A)).permanents.find(x => x.name === "Saavik, Stoic Student"), "TestB lets it resolve: Saavik is on the battlefield", (await ph()).held);
    await pb.evaluate(() => window.load && window.load()).catch(() => {}); await sleep(1200);

    console.log("damage and the end");
    // TestA acted in the window (it flashed Saavik), so it ends its own say with its button; TestB ends its with the chip. Both through the pages.
    let pressed = [];
    for (let i = 0; i < 10 && (await life(B)) > 0; i++) {
      for (const pg of [pa, pb]) await pg.evaluate(() => window.load && window.load()).catch(() => {});
      await sleep(900);
      if (await pa.locator("#alert [data-dmg]").count()) { if (!pressed.includes("A")) await shot(pa, "5-attacker-deal-damage"); await pa.click("#alert [data-dmg]"); pressed.push("A"); }
      else if (await pb.evaluate(() => document.body.classList.contains("u-on")) && await pb.locator(".u-bar .u-pass").count()) { await pb.click(".u-bar .u-pass"); pressed.push("B"); }
      await sleep(900);
    }
    check(pressed.includes("A") && pressed.includes("B"), "TestA pressed Deal combat damage and TestB the chip's Pass (each once it was theirs)", pressed);
    check(await life(B) <= 0, "the damage step: 3 + 1 into 4 life", { life: await life(B), step: (await ph()).step });
    await sleep(2500);
    for (const [n, pg] of [[A, pa], [B, pb]]) {
      await pg.evaluate(() => window.load && window.load()).catch(() => {}); await sleep(800);
      check(await pg.locator(".go-banner, .go-pill").count() > 0, `${n}'s page shows the game-over banner`, await pg.locator("body").evaluate(b => b.innerText.slice(0, 200)));
    }
    const g = await J(await fetch(BASE + "/api/game/summary"));
    check(g.over && g.winner === A && (g.totals || {})[B] <= 0, "the table's game-over: the winner and true totals", { w: g.winner, t: g.totals });
    await shot(pb, "6-game-over"); await shot(pa, "6-game-over-winner");
    check(!pa.__errors.length && !pb.__errors.length, "no page errors on either hand page", [...pa.__errors, ...pb.__errors]);
  } catch (e) { check(false, "the run finished", e && e.stack || String(e)); }
  finally { if (browser) await browser.close(); srv.kill(); }
  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join("; ")}` : "\nall passed");
  console.log("screenshots: " + SHOTS);
  process.exit(failed.length ? 1 : 0);
})();
