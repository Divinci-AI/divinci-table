// The D&D headset page (/xr) outside a headset: it loads, renders the table as a ~0.8 m diorama with every token,
// draws the wrist panel, and reads spoken dice ("roll a d20 for stealth"). Since 2026-11-15: the world around the
// table (sky, ground and a wooden table in the location's colours), the story parchment (what the DM and the table
// said, facing you), the headset's own voice (off until chosen, never re-speaking the backlog), condition marks on
// tags, and tags that step aside when creatures stand close. The immersive parts (hit-test placement,
// controllers, mic in a session, 72 fps) need the Quest itself — docs/DND-3D-GOAL.md D6's check.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/dnd_xr_e2e.cjs
const path = require("path");
const { spawn } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = require("./test_port.cjs").testPort();
const BASE = `http://127.0.0.1:${PORT}`;
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Michael,Sam,Ana", "--dm", "Michael", "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", TABLE_RESEARCH_DIR: os.tmpdir() }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 60; i++) { try { await fetch(BASE + "/api/dnd"); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
    await page.goto(`${BASE}/xr?player=Ana`);   // Michael (the DM) and Sam act from the test itself
    const ok = await page.waitForFunction(() => window.DNDXR && DNDXR.frames.length > 30, null, { timeout: 15000 }).then(() => true, () => false);
    check(ok, "the headset page renders (preview, no headset)");
    const toks = await page.evaluate(() => Object.keys(DNDXR.dnd.debug()));
    check(toks.includes("michael") && toks.includes("sam"), `every token is on the table (${toks.join(", ")})`);
    const across = await page.evaluate(() => { const { w, h } = DNDXR.dnd.size(); return Math.max(w, h) * 1.524 * DNDXR.table.scale.x; });
    check(Math.abs(across - 0.8) < 0.01, `as a diorama the map is ${across.toFixed(2)} m across`);
    const cases = await page.evaluate(() => [
      rollFromSpeech("Roll a d20 for stealth."), rollFromSpeech("I'm rolling 2d6 plus 3 for damage"),
      rollFromSpeech("roll a D 8"), rollFromSpeech("I attack the goblin"), rollFromSpeech("roll for initiative")]);
    check(JSON.stringify(cases[0]) === JSON.stringify({ dice: "d20", why: "stealth" }), `"Roll a d20 for stealth." → ${JSON.stringify(cases[0])}`);
    check(JSON.stringify(cases[1]) === JSON.stringify({ dice: "2d6+3", why: "damage" }), `"rolling 2d6 plus 3 for damage" → ${JSON.stringify(cases[1])}`);
    check(cases[2] && cases[2].dice === "d8", `"roll a D 8" → ${JSON.stringify(cases[2])}`);
    check(cases[3] === null && cases[4] === null, "talk that names no die is an action, not a roll");
    // ── the story, the world, the voice
    const api = async (p, body) => (await fetch(BASE + p, { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "xr-test" }, body: JSON.stringify(body) })).json();
    const mk = (await api("/api/seat/claim", { name: "Michael" })).key, sk = (await api("/api/seat/claim", { name: "Sam" })).key;
    check(await page.evaluate(() => DNDXR.world().sky && DNDXR.world().tabletop), "the preview has a world: sky, ground and a wooden table under the map");
    await api("/api/dnd/narrate", { by: "Michael", key: mk, text: "Cold air rolls up the crypt stairs as your lanterns catch carved names on the walls.\n"
      + 'TABLE: {"scene":"The Crypt Stair","location":"crypt","monsters":[{"name":"Skeleton 1","ac":13,"hp":13,"max_hp":13,"conditions":["asleep"]}]}' });
    const told = await page.waitForFunction(() => DNDXR.story.debug().parts.some(p => /crypt stairs/.test(p.text)) && DNDXR.story.debug().scene === "The Crypt Stair",
      null, { timeout: 6000 }).then(() => true, () => false);
    check(told, "the DM's narration and the scene's title reach the story parchment");
    check(await page.waitForFunction(() => DNDXR.world().theme === "crypt", null, { timeout: 5000 }).then(() => true, () => false),
      "the world takes the crypt's colours");
    check(await page.waitForFunction(() => Object.values(DNDXR.dnd.debug()).some(t => t.tag === "Skeleton 1|💤"), null, { timeout: 5000 }).then(() => true, () => false),
      "a sleeping skeleton's tag says 💤");
    await api("/api/dnd/act", { by: "Sam", key: sk, text: "I raise my lantern toward the carvings." });
    await api("/api/dnd/roll", { by: "Sam", key: sk, dice: "1d6+3+2d6", why: "sneak attack" });
    check(await page.waitForFunction(() => /lantern/.test(DNDXR.story.debug().said) && /sneak attack/.test(DNDXR.story.debug().roll), null, { timeout: 5000 }).then(() => true, () => false),
      "what a player did and the last roll (a sum: 1d6+3+2d6) show under the story");
    const facing = await page.evaluate(() => {
      const m = DNDXR.story.mesh, cam = DNDXR.camera, n = m.getWorldDirection(new m.position.constructor()), to = cam.position.clone().sub(m.position); to.y = 0; n.y = 0;
      return n.normalize().dot(to.normalize());
    });
    check(facing > 0.95, `the story faces the viewer (cos ${facing.toFixed(3)})`);
    const lifts = await page.evaluate(() => Object.entries(DNDXR.dnd.debug()).filter(([id]) => ["michael", "sam", "ana"].includes(id)).map(([, t]) => +t.lift.toFixed(3)));
    check(lifts.filter(l => l > 0.01).length >= 1, `tags of creatures standing together step aside (lifts ${lifts.join(", ")})`);
    check(await page.evaluate(() => DNDXR.story.voice.on === false && DNDXR.story.voice.log.length > 0),
      "the voice stays off until chosen (lines are queued for it, nothing is spoken)");
    await page.evaluate(() => DNDXR.story.voice.toggle());
    await page.reload();
    await page.waitForFunction(() => window.DNDXR && DNDXR.frames.length > 10, null, { timeout: 15000 });
    check(await page.evaluate(() => DNDXR.story.voice.on && DNDXR.story.voice.log.length === 0 && DNDXR.story.debug().parts.length > 0),
      "Voice on is remembered, and reopening the page shows the story so far without speaking it again");
    await api("/api/dnd/narrate", { by: "Michael", key: mk, text: "A bell tolls somewhere beneath your feet." });
    check(await page.waitForFunction(() => DNDXR.story.voice.log.some(l => /bell tolls/.test(l.text) && l.who === "narrator"), null, { timeout: 6000 }).then(() => true, () => false),
      "…and a new line is spoken in the narrator's voice");
    const ms = await page.evaluate(() => { const f = DNDXR.frames.slice(-120); return f.reduce((a, b) => a + b, 0) / f.length; });
    console.log(`  preview frame time (headless, software GL): ${ms.toFixed(1)} ms`);
    check(!errors.length, "no page errors" + (errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""));
  } finally {
    if (browser) await browser.close();
    srv.kill();
  }
  const ok = results.filter(r => r[0]).length;
  console.log(`\n${ok}/${results.length} headset-page checks passed`);
  process.exit(ok === results.length ? 0 : 1);
})();
