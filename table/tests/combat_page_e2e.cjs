// v6c combat on the hand page (game 5 retro: M1, M6, M19): ONE block panel for the whole attack, "Declare blocks" and a two-tap "No block",
// the window after blocks (Pass for the defender, "Deal combat damage" for the attacker), the "Confirm attack" label. A real server on a free
// port (never a live game's), a headless browser of its own (not the user's Chrome), seats TestA and TestB, every press through the page.
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/combat_page_e2e.cjs
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), net = require("net");
const TABLE = path.resolve(__dirname, "..");
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
const SHOTS = process.env.SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), "combat-shots-"));
const LIVE = [8800, 8801, 8820, 8830];
let failed = [];
const check = (ok, n, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(typeof d === "string" ? d : JSON.stringify(d)).slice(0, 500)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(LIVE.includes(p) ? freePort() : p)); }); });
(async () => {
  const PORT = await freePort(), BASE = `http://127.0.0.1:${PORT}`, A = "TestA", B = "TestB";
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "combat-page-"));
  const deckScript = `import json,os,sys
sys.path.insert(0, sys.argv[2])
import make_deck_json_shim as s
C={c['name']:c for c in s.load(os.path.expanduser('~/.divinci/star-trek-mtg/cards.json'))}
P={'Island':20,'Plains':20,'Xindi Surveyors':4,'Starfleet Security':4,'Tal Shiar Operative':4,'Mugato':4,'Wesley Crusher, Starfleet Cadet':4,'Pet Sehlat':4}
json.dump({'name':'t','commander':None,'startingLife':20,'mainBoard':[{**C[n],'count':k} for n,k in P.items()]},open(sys.argv[1],'w'))`;
  execFileSync(PY, ["-c", deckScript, tmp + "/deck.json", TABLE], { env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const srv = spawn(PY, [path.join(TABLE, "server.py"), "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "3",
    "--ai", `${A}|`, "--ai-deck", tmp + "/deck.json", "--ai", `${B}|`, "--ai-deck", tmp + "/deck.json", "--pilot", A, "--pilot", B, "--order", `${A},${B}`, "--seat-rate", "0"],
    { cwd: path.dirname(TABLE), env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", TABLE_NO_VOICE: "1", TABLE_HIGHROLL: "first", TABLE_CHECKIN: "off", STRICT_SEATS: "1", HF_HUB_OFFLINE: "1", ROUTER: "code",
      REPLIES: "template", NO_GEMMA: "1", SKIP_OLLAMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TYPESAFE_API_KEY: "" }, stdio: ["ignore", fs.openSync(tmp + "/log", "w"), fs.openSync(tmp + "/log", "a")] });
  let browser;
  const J = async r => { const t = await r.text(); try { return JSON.parse(t); } catch { return { error: t.slice(0, 200) }; } };
  let token = "", K = {};
  const host = async (a, b) => J(await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Brain-Token": token }, body: JSON.stringify(b) }));
  const seat = async (n, a, b = {}) => { for (let i = 0; i < 40; i++) { const r = await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[n], "User-Agent": "e2e-" + n }, body: JSON.stringify({ seat: n, ...b }) });
    const d = await J(r); if (r.status === 409 && /waiting on passes/.test(d.error || "") && !/blocks are in/.test(d.error || "")) { const p = await ph();
      for (const w of String(d.waiting || "").split(", ")) if (K[w] && w !== n) await fetch(`${BASE}/api/brain/pass`, { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[w], "User-Agent": "e2e-" + w }, body: JSON.stringify({ seat: w, player: p.player, step: p.step }) });
      await sleep(250); continue; } return { code: r.status, ...d }; } };
  const st = async n => J(await fetch(`${BASE}/api/brain/state?seat=${n}`, { headers: { "X-Seat-Key": K[n], "User-Agent": "e2e-" + n } }));
  const ph = async () => J(await fetch(`${BASE}/api/phase`));
  const life = async n => (await J(await fetch(`${BASE}/api/life`)))[n];
  const onto = async (n, name) => { await host("search", { seat: n, name }); await host("put", { seat: n, name, quiet: true }); return (await st(n)).permanents.filter(p => p.name === name).pop().id; };
  const toHand = async (n, name) => host("search", { seat: n, name });
  const emptyHands = async () => { for (const n of [A, B]) for (const h of (await st(n)).hand || []) await host("discard", { seat: n, name: h.name, quiet: true }); };
  const untilMain = async who => { for (let i = 0; i < 80; i++) { const p = await ph(); if (p.player === who && p.begun && p.step === "main 1") return; const nx = (p.passes || {}).next;
    if (K[nx]) await fetch(`${BASE}/api/brain/pass`, { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[nx], "User-Agent": "e2e-" + nx }, body: JSON.stringify({ seat: nx, player: p.player, step: p.step }) });
    await sleep(250); } };
  const turnTo = async who => { const p = await ph(); if (p.player && p.begun) await seat(p.player, "end", { text: "over" }); await untilMain(who); await emptyHands(); };
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    token = fs.readFileSync(tmp + "/token", "utf8").trim();
    for (const n of [A, B]) K[n] = (await J(await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "e2e-" + n }, body: JSON.stringify({ name: n }) }))).key;
    await fetch(BASE + "/api/phase/windows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: false, by: "host" }) });
    await fetch(BASE + "/api/phase/next", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[A] }, body: JSON.stringify({ by: A, key: K[A] }) });
    await untilMain(A); await emptyHands();
    const xs = await onto(A, "Xindi Surveyors"), ss = await onto(A, "Starfleet Security"), sehlat = await onto(A, "Pet Sehlat");
    const op = await onto(B, "Tal Shiar Operative"), mu = await onto(B, "Mugato");
    for (let i = 0; i < 4; i++) await onto(B, "Island");
    await turnTo(B); await turnTo(A);                       // past summoning sickness

    browser = await chromium.launch();
    const open = async n => { const ctx = await browser.newContext({ viewport: { width: 430, height: 900 }, userAgent: "e2e-" + n });   // phone-sized
      const page = await ctx.newPage(); page.__errors = []; page.on("pageerror", e => page.__errors.push(String(e)));
      await page.goto(`${BASE}/hand?player=${n}#key=${encodeURIComponent(K[n])}`); await page.waitForSelector("#stepbar", { timeout: 15000 }); await sleep(1500); return page; };
    const pa = await open(A), pb = await open(B);
    const shot = (p, n) => p.screenshot({ path: path.join(SHOTS, n + ".png") });
    const reload = async p => { await p.evaluate(() => window.load && window.load()).catch(() => {}); await sleep(900); };

    console.log("M19: the attacker's page: tick, then ONE confirmed declaration");
    await pa.click("#attackgo"); await sleep(400);
    const lab = await pa.locator("#attack").textContent().catch(() => "");
    check(/Confirm attack with 3/.test(lab), "after the Attack shortcut ticks every ready creature, the button reads Confirm attack with 3", lab);
    await pa.locator(`input[data-atk="${sehlat}"]`).uncheck().catch(async () => { await pa.evaluate(id => { const el = document.querySelector(`[data-atk="${id}"]`); if (el) el.click(); }, sehlat); }); await sleep(300);
    check(/Confirm attack with 2/.test(await pa.locator("#attack").textContent().catch(() => "")), "…a creature can be taken out before it is sent: Confirm attack with 2", await pa.locator("#attack").textContent().catch(() => ""));
    await pa.click("#attack"); await sleep(1200);
    let p = await ph();
    check(p.step === "declare attackers" && p.combat.attacks[0].stage === "blocks" && p.combat.attacks[0].attackers.length === 2 && await life(B) === 20,
      "the declaration is in: two attackers, the table waits for TestB's blocks", p.combat);
    await reload(pa);
    check(/Waiting for TestB to declare blocks/.test(await pa.locator("#alert").textContent()), "the attacker's page says who it is waiting for", await pa.locator("#alert").textContent());
    await shot(pa, "1-attacker-waiting");

    console.log("M1/M6: the defender's page: ONE panel for the whole attack");
    await reload(pb);
    const panel = pb.locator("#blockpanel");
    check(await panel.count() === 1 && /attacks you with 2 creatures: declare your blocks/.test(await panel.textContent()), "one block panel, naming both attackers", await pb.locator("#alert").textContent());
    check(await pb.locator("#blockpanel [data-blkm]").count() === 4 && await pb.locator("#blockpanel [data-take]").count() === 1 && await pb.locator("#blockpanel [data-blkgo]").count() === 0,
      "each attacker lists TestB's two creatures; one 'No block' for all; no Declare button until something is ticked", await panel.innerHTML());
    check(/No block: take it \(4\)/.test(await pb.locator("#blockpanel [data-take]").textContent()), "'No block: take it (4)': the whole attack's total", await pb.locator("#blockpanel [data-take]").textContent());
    check(/declare your blocks/.test(await pb.locator(".ca-bar").textContent().catch(() => "")), "the pinned banner says so too", await pb.locator(".ca-bar").textContent().catch(() => ""));
    await shot(pb, "2-block-panel");
    await pb.click(`#blockpanel [data-blkm="${op}"][data-batk="#${xs}"]`); await sleep(300);
    check(/Declare blocks \(1 blocker\)/.test(await pb.locator("#blockpanel [data-blkgo]").textContent().catch(() => "")) && await life(B) === 20 && (await ph()).combat.attacks[0].stage === "blocks",
      "ticking the Operative under Xindi Surveyors sends nothing yet: a Declare blocks (1 blocker) button appears", await panel.textContent());
    await pb.click(`#blockpanel [data-blkm="${op}"][data-batk="#${ss}"]`); await sleep(300);
    const marked = await pb.locator('#blockpanel [data-blkm][style*="outline"]').count();
    check(marked === 1, "a creature blocks one attacker: ticking it under the other attacker MOVES it", marked);
    await pb.click(`#blockpanel [data-blkm="${op}"][data-batk="#${xs}"]`); await sleep(300);
    await shot(pb, "3-block-ticked");
    await pb.click("#blockpanel [data-blkgo]"); await sleep(1500);
    const sa = await st(A), sb = await st(B);
    check(await life(B) === 19 && !sa.permanents.some(x => x.id === xs) && !sb.permanents.some(x => x.id === op) && (await ph()).step === "combat damage",
      "Declare blocks: one damage step: TestB takes 1 (20 -> 19), Xindi Surveyors and the Operative trade", { life: await life(B), step: (await ph()).step });
    await reload(pb);
    check(await pb.locator("#blockpanel").count() === 0, "the panel is gone", await pb.locator("#alert").textContent());

    console.log("T6: 'No block' takes two taps, and answers every attacker");
    await turnTo(B); await turnTo(A);
    await seat(A, "attack", { assign: { ["#" + ss]: B, ["#" + sehlat]: B } }); await sleep(600);
    await reload(pb);
    await pb.click("#blockpanel [data-take]"); await sleep(500);
    check(/Tap again: no blocks, take 5/.test(await pb.locator("#blockpanel [data-take]").textContent().catch(() => "")) && await life(B) === 19 && (await ph()).combat.attacks[0].stage === "blocks",
      "the first tap only arms it (Tap again: no blocks, take 5): nothing sent", await pb.locator("#alert").textContent());
    await pb.click("#blockpanel [data-take]"); await sleep(1500);
    check(await life(B) === 14, "the second tap: no blocks, 1 + 4 dealt together (19 -> 14)", await life(B));

    console.log("M1/M2: the window after blocks, on both pages");
    await turnTo(B); await turnTo(A);
    await toHand(B, "Wesley Crusher, Starfleet Cadet");     // TestB holds a flash card and four Islands: the window is theirs
    await seat(A, "attack", { assign: { ["#" + ss]: B } }); await sleep(600);
    await reload(pb);
    await pb.click("#blockpanel [data-take]"); await sleep(400); await pb.click("#blockpanel [data-take]"); await sleep(1500);
    p = await ph();
    check(p.step === "declare blockers" && p.combat.attacks[0].stage === "window" && await life(B) === 14, "blocks are in (none); TestB could cast Wesley: declare blockers, nothing dealt", { step: p.step, combat: p.combat });
    await reload(pb); await reload(pa);
    check(/Blocks are declared\. Before damage you may cast/.test(await pb.locator("#alert").textContent()) && await pb.locator("#alert [data-pass]").count() === 0
      && await pb.evaluate(() => document.body.classList.contains("u-on")),
      "the defender's page: 'Blocks are declared. Before damage you may cast …' with the ONE Pass in the priority chip (none in the note)", await pb.locator("#alert").textContent());
    check(/Blocks are in\. Waiting for TestB to pass/.test(await pa.locator("#alert").textContent()), "the attacker's page: 'Blocks are in. Waiting for TestB to pass; then the damage is dealt'", await pa.locator("#alert").textContent());
    await shot(pb, "4-window-defender"); await shot(pa, "5-window-attacker");
    await pb.click(".u-bar .u-pass"); await sleep(1800);
    check(await life(B) === 13 && (await ph()).step === "combat damage", "TestB presses Pass: the damage step (14 -> 13)", { life: await life(B), step: (await ph()).step });

    console.log("the attacker's own window: Deal combat damage");
    await turnTo(B); await turnTo(A);
    for (let i = 0; i < 4; i++) await onto(A, "Island");
    await toHand(A, "Wesley Crusher, Starfleet Cadet");     // now TestA holds the flash card
    await seat(A, "attack", { assign: { ["#" + ss]: B } }); await sleep(600);
    await seat(B, "block", { none: true }); await sleep(600);
    await reload(pa);
    check(await pa.locator("#alert [data-dmg]").count() === 1 && /Blocks are in: Starfleet Security unblocked/.test(await pa.locator("#alert").textContent()) && await life(B) === 13,
      "the attacker's page shows the blocks and a Deal combat damage button; nothing dealt yet", await pa.locator("#alert").textContent());
    await shot(pa, "6-deal-damage");
    await pa.click("#alert [data-dmg]"); await sleep(1800);
    check(await life(B) === 12 && (await ph()).step === "combat damage", "Deal combat damage: 13 -> 12", { life: await life(B), step: (await ph()).step });
    check(!pa.__errors.length && !pb.__errors.length, "no page errors on either hand page", [...pa.__errors, ...pb.__errors]);
  } catch (e) { check(false, "the run finished", e && e.stack || String(e)); }
  finally { if (browser) await browser.close(); srv.kill(); }
  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join("; ")}` : "\nall passed");
  console.log("screenshots: " + SHOTS);
  process.exit(failed.length ? 1 : 0);
})();
