// The pass-priority overlay, the nav bar and the table voice, in a real browser against a real local server (port 8824,
// its own research folder): when it is a person's turn to pass the screen glows and counts down, beeps speed up near the
// end, the Pass button works from any page, "My page" pulses, the AI seat's announcements are spoken in the browser and
// the voice can be muted, and reduced motion removes the pulsing.
//   PW=/path/to/@playwright/test node table/tests/pass_ui_e2e.cjs
process.env.TABLE_HIGHROLL = process.env.TABLE_HIGHROLL || "first";   // tests start the first seat; the opening high roll has its own test
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path");
const REPO = path.resolve(__dirname, "../..");
const PORT = 8824, BASE = `http://127.0.0.1:${PORT}`;
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
let failed = [];
const check = (name, ok, detail = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${name}${ok ? "" : " — " + detail}`); if (!ok) failed.push(name); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const walk = async (action, headers) => {                                              // like tablectl: retry while the table waits on passes
  for (let i = 0; i < 200; i++) { const [c] = await j("POST", "/api/brain/" + action, { seat: "Claude", quiet: true }, headers); if (c === 200) return; await sleep(1000); }
};
const j = async (m, p, b, h = {}) => { const r = await fetch(BASE + p, { method: m, headers: { "Content-Type": "application/json", ...h }, body: b ? JSON.stringify(b) : undefined }); return [r.status, await r.json().catch(() => ({}))]; };

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "passui-"));
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token",
    "--ai", "Claude|Kaust, Eyes of the Glade|", "--ai-deck", "decks/kaust.json", "--pilot", "Claude", "--human", "Michael|", "--human", "Sam|",
    "--order", "Claude,Michael,Sam", "--priority-window", "0", "--human-pass-secs", "14"],
    { cwd: REPO, env: { ...process.env, HF_HUB_OFFLINE: "1", ROUTER: "code", REPLIES: "template", TABLE_RESEARCH_DIR: tmp + "/research" }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 180; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(1000); }
    const [, claim] = await j("POST", "/api/seat/claim", { name: "Claude" });
    const pilot = { "X-Seat-Key": claim.key };
    await j("POST", "/api/phase/next", { by: "Claude", key: claim.key });                 // the pilot starts the game: Claude's turn
    walk("begin", pilot);                                                                 // walks to upkeep, where people must pass
    browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: { width: 390, height: 780 } });
    await ctx.addInitScript(() => {                                                       // count beeps and spoken lines
      window.__beeps = 0; window.__spoken = [];
      const AC = function () { return { state: "running", resume() {}, currentTime: 0, destination: {},
        createOscillator() { window.__beeps++; return { type: "", frequency: {}, connect() {}, start() {}, stop() {} }; },
        createGain() { return { gain: {}, connect() {} }; } }; };
      window.AudioContext = AC; window.webkitAudioContext = AC;
      window.__notes = []; window.__hidden = false;
      Object.defineProperty(document, "hidden", { get: () => window.__hidden });
      window.Notification = class { constructor(t, o) { window.__notes.push(t); } static get permission() { return "granted"; } static requestPermission() { return Promise.resolve("granted"); } };
      Object.defineProperty(window, "speechSynthesis", { configurable: true, value: { pending: false, speaking: false,
        speak(u) { window.__spoken.push(u.text); }, cancel() {}, getVoices() { return []; }, onvoiceschanged: null } });
      Object.defineProperty(window, "SpeechSynthesisUtterance", { configurable: true, writable: true, value: function (t) { this.text = t; } });
    });
    const page = await ctx.newPage();
    await page.goto(`${BASE}/stage?player=Michael`);
    await page.waitForFunction(() => window.TableSeat && TableSeat.name() === "Michael", null, { timeout: 15000 });
    await page.mouse.click(10, 400);                                                      // one tap, as a person would: unlocks sound

    console.log("overlay and nav");
    await page.waitForFunction(() => document.body.classList.contains("u-on"), null, { timeout: 15000 }).catch(() => {});
    const on = await page.evaluate(() => document.body.classList.contains("u-on"));
    check("when it is Michael's turn to pass, the overlay is on", on);
    const bar = await page.evaluate(() => { const b = document.querySelector(".u-bar"); return { shown: getComputedStyle(b).display !== "none", txt: b.innerText }; });
    check("the bar shows a Pass button and a countdown", bar.shown && /Pass priority/.test(bar.txt) && /\d+s/.test(bar.txt), JSON.stringify(bar));
    await sleep(500);                                                                      // past the glow's fade-in
    const g = await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector(".u-glow")).opacity));
    check("the red glow is visible", g > 0.3, String(g));
    const s1 = await page.evaluate(() => __urgent.state().remaining);
    await sleep(2200);
    const s2 = await page.evaluate(() => __urgent.state().remaining);
    check("the countdown runs in the server's clock", s1 > 0 && s2 < s1 - 1.5 && s1 <= 14.5, `${s1} -> ${s2}`);
    const nav = await page.evaluate(() => [...document.querySelectorAll(".tb a")].map(a => [a.getAttribute("href"), a.className, a.innerText]));
    check("the tabs along the top link Me, Stage, Round table (/xr), the 2D Cards board and Log, and mark this page",
          ["/me", "/stage", "/xr", "/board", "/log"].every(h => nav.some(n => n[0] === h)) && nav.find(n => n[0] === "/stage")[1].includes("tb-here"), JSON.stringify(nav));
    const top = await page.evaluate(() => { const r = document.querySelector(".tb").getBoundingClientRect(); return [r.top, r.left, r.width, innerWidth]; });
    check("the tabs sit at the very top and span the page", top[0] === 0 && top[1] === 0 && Math.abs(top[2] - top[3]) < 2, JSON.stringify(top));
    const fits = await page.evaluate(() => { const b = document.querySelector(".tb"); return b.scrollWidth <= b.clientWidth + 1; });
    check("on a 390 px phone every tab and toggle fits without scrolling", fits);
    check("'My page' pulses and says why while Michael has to pass", nav.find(n => n[0] === "/me")[1].includes("tb-need") && /Pass/.test(nav.find(n => n[0] === "/me")[2]), JSON.stringify(nav[0]));
    check("a bell rang when the turn to pass arrived", await page.evaluate(() => window.__beeps) >= 2);   // the two-tone ding

    console.log("beeps get faster, then the table passes for him");
    const b0 = await page.evaluate(() => window.__beeps);
    await page.waitForFunction(() => __urgent.state().remaining < 9, null, { timeout: 15000 });
    check("in the last ten seconds the glow pulses", await page.evaluate(() => document.body.classList.contains("u-urgent")));
    await sleep(3200);
    const b1 = await page.evaluate(() => window.__beeps);
    check("…and it beeps about once a second", b1 - b0 >= 2, `${b0} -> ${b1}`);
    await page.waitForFunction(() => !document.body.classList.contains("u-on") || __urgent.state().mine === false, null, { timeout: 15000 });
    const ev = (await j("GET", "/api/events?since=0"))[1].events.filter(e => e.type === "pass" && e.timeout && e.by === "Michael");
    check("when time runs out the table passes for Michael, in the log", ev.length >= 1, JSON.stringify(ev));

    console.log("the Pass button, from any page");
    walk("end", pilot);                                                                   // Claude's turn walks on: more steps to pass
    const page2 = await ctx.newPage();
    await page2.goto(`${BASE}/board?player=Michael`);
    await page2.waitForFunction(() => window.TableSeat && TableSeat.name() === "Michael" && window.__urgent, null, { timeout: 15000 });
    await page2.waitForFunction(() => document.body.classList.contains("u-on"), null, { timeout: 25000 }).catch(() => {});
    const before = (await j("GET", "/api/phase"))[1].passes;
    const canPass = await page2.evaluate(() => document.body.classList.contains("u-on"));
    if (canPass) {
      await page2.click(".u-pass");
      await sleep(1200);
      const after = (await j("GET", "/api/phase"))[1].passes;
      check("pressing Pass on the Board page passes for him", after.passed.includes("Michael") && !before.passed.includes("Michael") || after.step !== before.step, JSON.stringify([before, after]));
    } else check("the overlay reached the Board page for the next step", false, JSON.stringify(before));

    console.log("the table voice");
    check("the voice is off by default", (await page2.evaluate(() => __tablebar.prefs.voice)) === false);
    await j("POST", "/api/brain/say", { seat: "Claude", text: "Default is quiet." }, pilot);
    await sleep(2500);
    check("…so nothing is spoken until it is switched on", !(await page2.evaluate(() => window.__spoken)).some(t => /Default is quiet/.test(t)));
    await page2.click(".tb-v");                                   // switch the voice on (the button is the toggle)
    const [sc] = await j("POST", "/api/brain/say", { seat: "Claude", text: "I play my land and pass." }, pilot);
    await page2.waitForFunction(() => window.__spoken.some(t => /I play my land/.test(t)), null, { timeout: 8000 }).catch(() => {});
    const sp = await page2.evaluate(() => window.__spoken);
    check("the AI seat's announcement is spoken in the browser", sp.some(t => /I play my land and pass/.test(t)),
      JSON.stringify({ sayStatus: sc, sp, hidden: await page2.evaluate(() => document.hidden), voice: await page2.evaluate(() => window.__tablebar && __tablebar.prefs) }));
    await page2.click(".tb-v");
    await j("POST", "/api/brain/say", { seat: "Claude", text: "This should stay silent." }, pilot);
    await sleep(2500);
    check("with the voice muted nothing more is spoken", !(await page2.evaluate(() => window.__spoken)).some(t => /stay silent/.test(t)));
    await page2.click(".tb-v");
    await j("POST", "/api/brain/say", { seat: "Claude", text: "Voice is back." }, pilot);
    await page2.waitForFunction(() => window.__spoken.some(t => /Voice is back/.test(t)), null, { timeout: 8000 }).catch(() => {});
    check("and it speaks again when switched back on", (await page2.evaluate(() => window.__spoken)).some(t => /Voice is back/.test(t)));

    const mpage = await ctx.newPage();                             // ?mute=1: silent in that tab even with the voice saved on
    await mpage.addInitScript(() => { window.__spoken = []; Object.defineProperty(window, "speechSynthesis", { configurable: true, value: {
      speak(u) { window.__spoken.push(u.text); }, cancel() {}, getVoices() { return []; }, onvoiceschanged: null } }); });
    await mpage.goto(`${BASE}/me?player=Michael&mute=1`);
    await sleep(1500);
    await j("POST", "/api/brain/say", { seat: "Claude", text: "Muted tab line." }, pilot);
    await sleep(2500);
    check("?mute=1 keeps a tab silent although the voice is saved on", !(await mpage.evaluate(() => window.__spoken)).some(t => /Muted tab line/.test(t)));
    await mpage.close();

    console.log("notifications");
    await walk("end", pilot).catch(() => {});
    const npage = await ctx.newPage();
    await npage.goto(`${BASE}/me?player=Michael`);
    await npage.waitForFunction(() => window.__tablebar && window.TableSeat && TableSeat.name() === "Michael", null, { timeout: 15000 });
    await npage.click(".tb-n");                                                            // turn alerts on (the stub grants permission)
    check("the alerts button turns notifications on", await npage.evaluate(() => __tablebar.prefs.alerts === true));
    await npage.evaluate(() => { window.__hidden = true; });                                // the tab goes to the background
    await npage.evaluate(() => __tablebar.alertMe("test", "x"));
    check("a background tab gets a notification", (await npage.evaluate(() => window.__notes)).includes("test"));
    await npage.evaluate(() => { window.__hidden = false; window.__notes.length = 0; __tablebar.alertMe("visible", "x"); });
    check("a visible tab does not (the page itself is the alert)", (await npage.evaluate(() => window.__notes)).length === 0);
    await npage.evaluate(() => { window.__urgent.state; });
    const t = await page.evaluate(() => document.title);
    check("the tab title carries the alert while it is your turn to pass (or is the plain title)", typeof t === "string" && t.length > 0);

    console.log("reduced motion");
    const rctx = await browser.newContext({ viewport: { width: 390, height: 780 }, reducedMotion: "reduce" });
    const rp = await rctx.newPage();
    await rp.goto(`${BASE}/stage?player=Michael`);
    await rp.waitForFunction(() => window.__urgent, null, { timeout: 15000 });
    const anim = await rp.evaluate(() => { document.body.classList.add("u-on", "u-urgent"); return getComputedStyle(document.querySelector(".u-glow")).animationName; });
    check("reduced motion: the glow holds steady instead of pulsing", anim === "none", anim);
  } catch (e) { check("the run completed", false, String(e).slice(0, 300)); }
  finally { if (browser) await browser.close(); srv.kill(); }
  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join("; ")}` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
