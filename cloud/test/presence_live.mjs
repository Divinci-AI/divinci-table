// LIVE test of /api/xr/presence against a real room (not in run_all: it needs a room and two claimed seats).
//   node cloud/test/presence_live.mjs <room id> <seat key A> <seat key B>
const [room, keyA, keyB] = process.argv.slice(2);
const url = "wss://table.divinci.ai/api/xr/presence";
const open = () => new Promise((ok, bad) => { const ws = new WebSocket(url, { headers: { Cookie: `room=${room}` } }); ws.onopen = () => ok(ws); ws.onerror = e => bad(e); });
const next = (ws, ms = 6000) => new Promise(ok => { const t = setTimeout(() => ok(null), ms); ws.addEventListener("message", e => { clearTimeout(t); ok(JSON.parse(e.data)); }, { once: true }); });
let failed = 0; const check = (n, c, d = "") => { if (!c) failed++; console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : " — " + JSON.stringify(d))); };
const A = await open(), B = await open();
const wa = next(A, 20000), wb = next(B, 20000);             // listen first, then send: the hellos can race
A.send(JSON.stringify({ t: "auth", key: keyA })); B.send(JSON.stringify({ t: "auth", key: keyB }));
const [ha, hb] = await Promise.all([wa, wb]);
check("both seats authenticate", ha?.t === "hello" && hb?.t === "hello", [ha, hb]);
const pose = { t: "pose", h: [0.1, 1.3, 1.15, 0, 0, 0, 1], l: [0.3, 1.0, 1.0, 0, 0, 0, 1], r: null, mode: "vr" };
const got = next(B); A.send(JSON.stringify(pose)); const m = await got;
check("B sees A's head and left hand, tagged with A's seat", m?.t === "pose" && m.seat === ha?.seat && m.h[2] === 1.15 && m.l && !m.r, m);
const none = next(B, 2500); A.send(JSON.stringify({ t: "pose", h: [0, 1, 999, 0, 0, 0, 1] })); check("a pose 999 m away is dropped", (await none) === null);
const X = await open(); const gone = new Promise(ok => (X.onclose = e => ok(e.code)));
X.send(JSON.stringify({ t: "pose", h: [0, 1, 0, 0, 0, 0, 1] }));
check("a socket that sends a pose before authenticating is closed (policy violation)", (await gone) === 1008);
const Y = await open(); const yGone = new Promise(ok => (Y.onclose = e => ok(e.code)));
Y.send(JSON.stringify({ t: "auth", key: "made-up" }));
check("a made-up key is refused and closed", (await yGone) === 1008);
const left = next(B); A.close(); const lv = await left;
check("B is told when A leaves", lv?.t === "leave" && lv.seat === ha?.seat, lv);
B.close(); console.log(failed ? `${failed} FAILED` : "ok"); process.exit(failed ? 1 : 0);
