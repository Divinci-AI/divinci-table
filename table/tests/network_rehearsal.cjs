// Network rehearsal: how does a theater-mode D&D room behave on a bad network (conference Wi-Fi, a phone hotspot)?
// Pages long-poll /api/events?since=N&wait=20 (table/dnd.html tick/pollLoop; core.py Events.wait_since). For each
// network profile, three phones (separate browser contexts) join one theater room and play a short script of table-talk
// actions (/api/chat, which fills the public log and never calls the DM). We record, per profile:
//   - delay from a player's action to EVERY OTHER device showing it (median / p95 / max)
//   - failed requests (requestfailed, HTTP >= 400) and hung requests (held far longer than the page asked for)
//   - whether any action never arrived, or took > 15 s to (a device that "stopped updating")
//   - whether the page fell back to short polling (an /api/events request WITHOUT ?wait after page load)
//
// Exit 1 if any action never reached some device, or any delay exceeded 15 s.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/network_rehearsal.cjs
//   NET_PROFILES=clean,proxy8s  to run a subset;  NET_SCALE=0.5 to shorten the timeline.
//
// HONESTY NOTES (also printed with the results):
//  * Chromium only. Throttling is CDP Network.emulateNetworkConditions per page: latency is added per request,
//    throughput is capped, offline fails every request (and cuts held long-polls). It models RTT/bandwidth/outage.
//    It does NOT model packet loss, TCP slow-start, retransmit stalls, or the radio waking up on a phone.
//  * "bad-wifi 400 ms RTT" is applied as CDP latency=400 (extra delay on each request/response exchange).
//  * The 8-second proxy is emulated with page.route on /api/events requests carrying wait>=8: the request is forwarded
//    to the real server with route.fetch({timeout: 8000}); if the server has not answered within 8 s the browser request is
//    aborted with "connectionreset", as a transparent proxy that RSTs idle upstream connections would. Because
//    route.fetch runs in Node, those events requests bypass CDP throttling (a flat ~0 ms RTT on that one endpoint).
//    It does not simulate a proxy answering 504 or buffering a half-response.
//  * Going offline: CDP "offline" only fails NEW requests; a long poll already held keeps going and completes after the link
//    returns (measured here, and unlike a real dead link). So in hotspot-flaky every /api/events request is also routed through
//    page.route (latency added by hand, no bandwidth cap on those tiny bodies) and the harness aborts the in-flight ones with
//    "internetdisconnected" the moment the link drops.
//  * A person who taps while offline taps again: the harness retries a failed post every 1 s (up to 40 s) and measures
//    from the FIRST tap. The page itself does not retry a failed post.
const path = require("path");
const fs = require("fs");
const net = require("net");
const { spawn } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "net-rehearsal-"));
const SCALE = Number(process.env.NET_SCALE || 1);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const q = (xs, p) => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.ceil(p * s.length) - 1)]; };
const STALL_MS = 15000;

// Player actions (seconds into the run). Quiet stretches are deliberate: a long poll is only HELD when nothing happens.
const SCHEDULE = [1, 2.5, 4, 10, 15, 16.5, 23, 25].map(s => s * SCALE * 1000);
const RUN_MS = 27000 * SCALE;
// proxy profile: quiet gaps of 11-12 s so held long polls actually reach the proxy's 8 s limit (the busy schedule never does)
const SPARSE = [1, 3, 14, 15.5, 28, 29.5].map(s => s * SCALE * 1000);
const SPARSE_RUN_MS = 31000 * SCALE;
const OFFLINE = [[7000 * SCALE, 6000 * SCALE], [19000 * SCALE, 6000 * SCALE]];     // hotspot-flaky: [start, length] ms
const MBIT = m => (m * 1e6) / 8;                                                     // Mbit/s -> bytes/s

const PROFILES = {
  "clean":         { label: "clean (baseline)",   cdp: { latency: 0, down: -1, up: -1 } },
  "good-wifi":     { label: "good-wifi 30ms",     cdp: { latency: 30, down: -1, up: -1 } },
  "bad-wifi":      { label: "bad-wifi 400ms 1.5M", cdp: { latency: 400, down: MBIT(1.5), up: MBIT(0.75) } },
  "hotspot-flaky": { label: "hotspot 250ms 1M +off", cdp: { latency: 250, down: MBIT(1), up: MBIT(0.5) }, offline: OFFLINE },
  "proxy8s":       { label: "proxy kills >8s held", cdp: { latency: 30, down: -1, up: -1 }, proxyKillMs: 8000 },
};
const WANT = (process.env.NET_PROFILES || Object.keys(PROFILES).join(",")).split(",");

async function freePort(start) {
  for (let p = start; p < start + 30; p++) {
    const ok = await new Promise(res => { const s = net.createServer(); s.once("error", () => res(false)); s.once("listening", () => s.close(() => res(true))); s.listen(p, "127.0.0.1"); });
    if (ok) return p;
  }
  throw new Error("no free port near " + start);
}

(async () => {
  const PORT = await freePort(8812);
  if (PORT === 8800) throw new Error("refusing to use 8800");
  const BASE = `http://127.0.0.1:${PORT}`;
  const SCRIPT = path.join(TMP, "dm.txt"); fs.writeFileSync(SCRIPT, "The tavern hums.");
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Ana,Ben,Cy", "--mode", "theater", "--dm-backend", "script:" + SCRIPT, "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", HF_HUB_OFFLINE: "1", TABLE_RESEARCH_DIR: path.join(TMP, "research") }, stdio: "ignore" });
  let browser; const rows = [];
  try {
    for (let i = 0; i < 80; i++) { try { await (await fetch(BASE + "/api/dnd")).json(); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true });

    for (const key of WANT) {
      const prof = PROFILES[key]; if (!prof) { console.error("unknown profile " + key); continue; }
      console.log(`\n── ${prof.label}`);
      const row = { key, label: prof.label, delays: [], failed: 0, hung: 0, never: 0, shortPoll: 0, longPollsFailed: 0, longPolls: 0, perDevice: {}, notes: [] };
      const devs = [];
      for (const who of ["Ana", "Ben", "Cy"]) {
        const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
        const page = await ctx.newPage();
        const d = { who, ctx, page, pending: new Map(), reqs: 0, longFail: 0, short: 0, loaded: false, offline: false };
        const errs = []; page.on("pageerror", e => errs.push(e.message)); d.errs = errs;
        // request bookkeeping (only after the page is ready, so the initial load never counts as a "short poll")
        page.on("request", r => { if (!d.loaded) return; const u = r.url(); d.pending.set(r, Date.now());
          if (u.includes("/api/events")) { if (/[?&]wait=/.test(u)) row.longPolls++; else d.short++; } });
        page.on("requestfinished", r => d.pending.delete(r));
        page.on("requestfailed", r => { if (!d.loaded) return; d.pending.delete(r); row.failed++; if (/\/api\/events\?.*wait=/.test(r.url())) d.longFail++; });
        page.on("response", r => { if (d.loaded && r.status() >= 400) row.failed++; });
        await page.goto(`${BASE}/?player=${who}`);
        await page.waitForFunction(n => typeof S !== "undefined" && S && S.map && window.TableSeat && TableSeat.name() === n && !DNDVOICE.firstBatch, who);
        // when each action id first appears in the log, on this device
        await page.evaluate(() => {
          window.__seen = {}; const log = document.getElementById("log");
          const scan = () => { const m = log.textContent.match(/\[na-[a-z0-9]+-\d+\]/g) || []; for (const id of m) if (!(id in window.__seen)) window.__seen[id] = Date.now(); };
          new MutationObserver(scan).observe(log, { childList: true, subtree: true, characterData: true }); scan();
        });
        d.cdp = await ctx.newCDPSession(page);
        d.setNet = async (offline) => d.cdp.send("Network.emulateNetworkConditions", {
          offline, latency: prof.cdp.latency, downloadThroughput: prof.cdp.down, uploadThroughput: prof.cdp.up });
        d.killers = new Set();
        if (prof.proxyKillMs || prof.offline) await page.route(/\/api\/events\?/, async route => {   // see HONESTY NOTES: CDP offline does not cut an in-flight held request
          const u = new URL(route.request().url()); const wait = Number(u.searchParams.get("wait") || 0);
          if (d.offline) return route.abort("internetdisconnected").catch(() => {});
          const lat = prof.cdp.latency; let kill; const killed = new Promise(res => { kill = res; d.killers.add(res); });
          try {
            if (lat) await sleep(lat);
            const limit = prof.proxyKillMs && wait >= 8 ? prof.proxyKillMs : 60000;
            const got = await Promise.race([route.fetch({ timeout: limit }).then(r => ({ r })), killed.then(() => ({ cut: true }))]);
            if (got.cut) return route.abort("internetdisconnected").catch(() => {});
            if (lat) await sleep(lat);
            await route.fulfill({ response: got.r });
          } catch (e) {
            if (/timeout/i.test(String(e.message)) && prof.proxyKillMs) { d.proxyKills = (d.proxyKills || 0) + 1; await route.abort("connectionreset").catch(() => {}); }
            else await route.abort("failed").catch(() => {});
          } finally { d.killers.delete(kill); }
        });
        await d.setNet(false);
        d.loaded = true;
        devs.push(d);
      }

      // the run
      const RUNID = key.replace(/[^a-z0-9]/g, "") + Math.random().toString(36).slice(2, 6);
      const posts = [];            // {id, poster, t0}
      const t0 = Date.now();
      const timers = [];
      if (prof.offline) for (const [at, len] of prof.offline) {
        timers.push(setTimeout(async () => { for (const d of devs) { d.offline = true; await d.setNet(true).catch(() => {}); for (const k of [...d.killers]) k(); } }, at));   // killers: the TCP connection dies with the link
        timers.push(setTimeout(async () => { for (const d of devs) { d.offline = false; await d.setNet(false).catch(() => {}); } }, at + len));
      }
      const actions = (prof.proxyKillMs ? SPARSE : SCHEDULE).map((at, i) => (async () => {
        await sleep(Math.max(0, at - (Date.now() - t0)));
        const poster = devs[i % devs.length], id = `[na-${RUNID}-${String(i).padStart(3, "0")}]`, rec = { id, poster: poster.who, t0: Date.now(), ok: false };
        posts.push(rec);
        for (let attempt = 0; attempt < 40 && !rec.ok; attempt++) {                       // a person taps again when nothing happens
          try { const r = await poster.page.evaluate(t => post("/api/chat", { text: t }), `${id} hello`); rec.ok = !!(r && r.ok); }
          catch { rec.ok = false; }
          if (!rec.ok) await sleep(1000);
        }
      })());
      await Promise.all(actions);
      await sleep(Math.max(0, (prof.proxyKillMs ? SPARSE_RUN_MS : RUN_MS) - (Date.now() - t0)));
      timers.forEach(clearTimeout);
      // let everything land: wait up to 20 s more for the last stragglers (online, profile still applied)
      for (const d of devs) if (d.offline) { d.offline = false; await d.setNet(false).catch(() => {}); }
      const drainUntil = Date.now() + 20000;
      for (;;) {
        let missing = 0;
        for (const d of devs) { const seen = await d.page.evaluate(() => window.__seen); for (const p of posts) if (d.who !== p.poster && !(p.id in seen)) missing++; }
        if (!missing || Date.now() > drainUntil) break;
        await sleep(250);
      }
      // collect
      for (const d of devs) {
        const seen = await d.page.evaluate(() => window.__seen);
        const mine = [];
        for (const p of posts) {
          if (d.who === p.poster) continue;
          if (!(p.id in seen)) { row.never++; row.notes.push(`${p.id} by ${p.poster} NEVER reached ${d.who}`); continue; }
          const ms = seen[p.id] - p.t0; mine.push(ms); row.delays.push(ms);
          if (ms > STALL_MS) row.notes.push(`${p.id} by ${p.poster} took ${(ms / 1000).toFixed(1)}s to reach ${d.who}`);
        }
        const now = Date.now();
        for (const [r, since] of d.pending) {                    // still pending: legit only for the one in-flight long poll (<= ~25 s)
          const long = /wait=/.test(r.url()); if (now - since > (long ? 30000 : 12000)) row.hung++;
        }
        row.perDevice[d.who] = { max: mine.length ? Math.max(...mine) : null, short: d.short, longFail: d.longFail, kills: d.proxyKills || 0, errs: d.errs.length };
        row.shortPoll += d.short; row.longPollsFailed += d.longFail; row.kills = (row.kills || 0) + (d.proxyKills || 0);
        if (d.errs.length) row.notes.push(`${d.who}: page errors: ${d.errs.slice(0, 2).join(" | ")}`);
      }
      // hung/long requests that completed but were held way too long can't be seen after the fact; they show up as delay.
      const stalled = row.never > 0 || row.delays.some(x => x > STALL_MS);
      row.stalled = stalled;
      console.log(`   ${row.delays.length} deliveries; median ${q(row.delays, .5)} ms, p95 ${q(row.delays, .95)} ms, max ${Math.max(...row.delays, 0)} ms; failed ${row.failed}; never ${row.never}`);
      rows.push(row);
      for (const d of devs) await d.ctx.close();
    }
  } finally {
    if (browser) await browser.close();
    srv.kill();
    fs.rmSync(TMP, { recursive: true, force: true });
  }

  // ── results table
  const f = v => v === null || v === undefined ? "-" : String(v);
  const cols = ["profile", "n", "median ms", "p95 ms", "max ms", "failed", "hung", "lp-fail", "never", "stall>15s", "short-poll fallback"];
  const lines = rows.map(r => [r.label, r.delays.length, q(r.delays, .5), q(r.delays, .95), r.delays.length ? Math.max(...r.delays) : null, r.failed, r.hung, r.longPollsFailed,
    r.never, r.stalled ? "YES" : "no", r.shortPoll ? `yes (${r.shortPoll} reqs)` : "no (0 reqs)"].map(f));
  const w = cols.map((c, i) => Math.max(c.length, ...lines.map(l => l[i].length)));
  const fmt = l => l.map((c, i) => c.padEnd(w[i])).join("  ");
  console.log("\n" + fmt(cols)); console.log(w.map(n => "-".repeat(n)).join("  ")); for (const l of lines) console.log(fmt(l));
  console.log("\ncolumns: n = action->other-device deliveries; failed = requestfailed + HTTP>=400 after page load (includes deliberate offline/proxy cuts);");
  console.log("hung = requests still unresolved at the end (>30 s for a long poll, >12 s otherwise); lp-fail = failed long-poll requests;");
  console.log("short-poll fallback = /api/events requests WITHOUT ?wait after page load.");
  for (const r of rows) {
    if (r.kills) console.log(`note: ${r.label}: the simulated proxy cut ${r.kills} held requests`);
    for (const n of r.notes) console.log(`note: ${r.label}: ${n}`);
  }
  console.log("\nsimulation limits: CDP throttling = latency + bandwidth + offline only (no packet loss, no TCP slow-start/retransmit, no radio wake-up);");
  console.log("proxy8s = page.route + route.fetch(timeout 8s) then abort 'connectionreset' on /api/events?wait>=8 (those requests bypass CDP throttling);");
  console.log("hotspot-flaky also routes /api/events by hand (latency added manually; in-flight held polls aborted when the link drops, since CDP offline does not cut them);");
  console.log("a tap during an outage is retried by the harness every 1 s and timed from the first tap; Chromium only.");
  const bad = rows.some(r => r.stalled);
  console.log(bad ? "\nFAIL: a device stopped updating (>15 s) or an action never arrived" : "\nPASS: every action reached every device within 15 s on every profile");
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error("crashed:", e); process.exit(2); });
