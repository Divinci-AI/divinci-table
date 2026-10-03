/**
 * divinci-table in the cloud.
 *
 *   /                 public lobby: open rooms, and a form to start one
 *   POST /lobby/rooms create a room (rate-limited per address; a global cap on live rooms)
 *   /r/<id>           join a room: sets the `room` cookie and opens its stage
 *   everything else   proxied to that room's container (the unchanged Python table server)
 *
 * The table's pages use absolute paths (/api/phase, /me, /assets/…), so a cookie — not a path prefix —
 * picks the room. Every proxied request carries X-Forwarded-For, which the table server reads as "a
 * remote device": it gets only the endpoints a phone on the Wi-Fi gets, never the host-only ones.
 */
import { Container } from "@cloudflare/containers";
import { DurableObject } from "cloudflare:workers";

interface Env {
	ROOM: DurableObjectNamespace<TableRoom>;
	LOBBY: DurableObjectNamespace<Lobby>;
	DIVINCI_FUSION_API_KEY?: string;
	FUSION_CONFIG?: string;
}

interface AiSeat { name: string; commander: string; deck: string }
interface RoomConfig { humans: string[]; ai: AiSeat[]; game: "magic" }
interface RoomInfo { id: string; title: string; game: string; humans: string[]; ai: string[]; created: number }

// AI opponents a public room may seat. Each is played by a Divinci release (one per deck).
const AI_DECKS: Record<string, { commander: string; deck: string }> = {
	tuvasa: { commander: "Tuvasa the Sunlit", deck: "decks/tuvasa.json" },
	kaust: { commander: "Kaust, Eyes of the Glade", deck: "decks/kaust.json" },
	ellivere: { commander: "Ellivere of the Wild Court", deck: "decks/ellivere.json" },
};
const ROOM_TTL_MS = 6 * 3600_000;      // a room is listed for six hours
const MAX_LIVE_ROOMS = 8;              // matches containers.max_instances
const MAX_CREATES_PER_HOUR = 3;        // per address
const NAME_RE = /^[\p{L}\p{N} '._-]{1,24}$/u;

// ── one table room = one container ────────────────────────────────────────────────────────────
export class TableRoom extends Container<Env> {
	defaultPort = 8800;
	sleepAfter = "3h";
	enableInternet = true;             // Scryfall card names, ANU/drand randomness, the Divinci API

	async fetch(request: Request): Promise<Response> {
		const url = new URL(request.url);
		if (url.pathname === "/__room/setup" && request.method === "POST") {
			await this.ctx.storage.put("config", await request.json());
			return new Response("ok");
		}
		const config = await this.ctx.storage.get<RoomConfig>("config");
		if (!config) return new Response("This room doesn't exist (or has expired).", { status: 404 });
		this.envVars = {
			ROOM_CONFIG: JSON.stringify(config),
			...(this.env.DIVINCI_FUSION_API_KEY ? { DIVINCI_FUSION_API_KEY: this.env.DIVINCI_FUSION_API_KEY } : {}),
			...(this.env.FUSION_CONFIG ? { FUSION_CONFIG: this.env.FUSION_CONFIG } : {}),
		};
		await this.startAndWaitForPorts(this.defaultPort);
		return this.containerFetch(request, this.defaultPort);
	}
}

// ── the public room list ──────────────────────────────────────────────────────────────────────
export class Lobby extends DurableObject<Env> {
	async list(): Promise<RoomInfo[]> {
		const now = Date.now();
		const rooms = (await this.ctx.storage.get<RoomInfo[]>("rooms")) ?? [];
		const live = rooms.filter((r) => now - r.created < ROOM_TTL_MS);
		if (live.length !== rooms.length) await this.ctx.storage.put("rooms", live);
		return live;
	}

	/** Register a room, or say why not. */
	async add(room: RoomInfo, addr: string): Promise<string | null> {
		const now = Date.now();
		const rooms = await this.list();
		if (rooms.length >= MAX_LIVE_ROOMS) return "All tables are busy right now. Join an open room, or try again later.";
		const key = "creates:" + addr;
		const recent = ((await this.ctx.storage.get<number[]>(key)) ?? []).filter((t) => now - t < 3600_000);
		if (recent.length >= MAX_CREATES_PER_HOUR) return "You've started several rooms this hour. Please join one instead.";
		await this.ctx.storage.put(key, [...recent, now]);
		await this.ctx.storage.put("rooms", [...rooms, room]);
		return null;
	}
}

// ── the Worker ────────────────────────────────────────────────────────────────────────────────
export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		const url = new URL(request.url);
		const lobby = env.LOBBY.get(env.LOBBY.idFromName("lobby"));

		if (url.pathname === "/" && request.method === "GET") {
			return html(lobbyPage(await lobby.list(), url.searchParams.get("error") ?? ""));
		}
		if (url.pathname === "/lobby/rooms" && request.method === "POST") {
			return createRoom(request, env, lobby);
		}
		const join = url.pathname.match(/^\/r\/([a-z0-9]{8})\/?$/);
		if (join) {
			return new Response(null, {
				status: 302,
				headers: {
					Location: "/stage",
					"Set-Cookie": `room=${join[1]}; Path=/; Max-Age=${ROOM_TTL_MS / 1000}; Secure; SameSite=Lax`,
				},
			});
		}
		if (url.pathname.startsWith("/__room")) return new Response("Not found", { status: 404 });

		const room = (request.headers.get("Cookie") ?? "").match(/(?:^|;\s*)room=([a-z0-9]{8})/)?.[1];
		if (!room) return Response.redirect(url.origin + "/", 302);
		const headers = new Headers(request.headers);
		for (const h of ["X-Forwarded-For", "Forwarded", "X-Real-IP", "True-Client-IP"]) headers.delete(h);
		headers.set("X-Forwarded-For", request.headers.get("CF-Connecting-IP") ?? "unknown");   // always "remote"
		return env.ROOM.get(env.ROOM.idFromName(room)).fetch(new Request(request, { headers }));
	},
} satisfies ExportedHandler<Env>;

async function createRoom(request: Request, env: Env, lobby: DurableObjectStub<Lobby>): Promise<Response> {
	const form = await request.formData();
	const back = (msg: string) => Response.redirect(new URL("/?error=" + encodeURIComponent(msg), request.url).toString(), 303);
	const humans = String(form.get("humans") ?? "").split(",").map((s) => s.trim()).filter(Boolean);
	if (humans.length < 1 || humans.length > 4) return back("Name between one and four human seats.");
	if (!humans.every((h) => NAME_RE.test(h))) return back("Seat names: letters, numbers and spaces, up to 24 characters.");
	const ai: AiSeat[] = [];
	for (const [i, pick] of form.getAll("ai").map(String).entries()) {
		const d = AI_DECKS[pick];
		if (d) ai.push({ name: i === 0 ? "Fusion" : `Fusion ${i + 1}`, ...d });
	}
	if (ai.length > 2 || humans.length + ai.length < 2 || humans.length + ai.length > 4) {
		return back("A table seats two to four players, with at most two AI players.");
	}
	if (ai.length && !env.DIVINCI_FUSION_API_KEY) return back("AI players aren't available on this server yet.");
	const title = String(form.get("title") ?? "").trim().slice(0, 40) || `${humans[0]}'s table`;
	const id = [...crypto.getRandomValues(new Uint8Array(8))].map((b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");
	const why = await lobby.add({ id, title, game: "magic", humans, ai: ai.map((a) => `${a.name} (${a.commander})`), created: Date.now() },
		request.headers.get("CF-Connecting-IP") ?? "unknown");
	if (why) return back(why);
	await env.ROOM.get(env.ROOM.idFromName(id)).fetch(new Request("https://room/__room/setup", {
		method: "POST", body: JSON.stringify({ humans, ai, game: "magic" } satisfies RoomConfig),
	}));
	return Response.redirect(new URL("/r/" + id, request.url).toString(), 303);
}

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const html = (body: string) => new Response(body, { headers: { "Content-Type": "text/html; charset=utf-8" } });

function lobbyPage(rooms: RoomInfo[], error: string): string {
	const list = rooms.length
		? rooms.map((r) => `<li><a href="/r/${r.id}">${esc(r.title)}</a>
			<span>Magic: Commander · ${esc([...r.humans, ...r.ai].join(", "))} · ${Math.max(1, Math.round((Date.now() - r.created) / 60000))} min ago</span></li>`).join("")
		: "<li class=empty>No open tables yet. Start one.</li>";
	return `<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Divinci Table</title><style>
:root{--bg:#f6f5f2;--fg:#1d1c1a;--muted:#6b6862;--card:#fff;--line:#e2dfd8;--accent:#5b3fd1}
@media (prefers-color-scheme:dark){:root{--bg:#121214;--fg:#ecebe8;--muted:#9a978f;--card:#1c1c20;--line:#2c2c32;--accent:#9d86ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:720px;margin:0 auto;padding:32px 16px}h1{margin:0 0 4px;font-size:28px}p.lead{color:var(--muted);margin:0 0 28px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 18px 8px;margin-bottom:20px}
h2{font-size:17px;margin:0 0 10px}ul{list-style:none;margin:0;padding:0}li{padding:10px 0;border-top:1px solid var(--line)}
li:first-child{border-top:0}li a{font-weight:600;color:var(--accent);text-decoration:none}li span{display:block;color:var(--muted);font-size:14px}
li.empty{color:var(--muted)}label{display:block;margin:10px 0 4px;font-weight:600;font-size:14px}
input[type=text]{width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--fg);font:inherit}
.ai label{display:flex;gap:8px;font-weight:400;margin:6px 0}button{margin:14px 0 12px;padding:10px 18px;border:0;border-radius:10px;background:var(--accent);color:#fff;font:600 15px system-ui;cursor:pointer}
.err{background:#fde8e8;color:#8a1c1c;border-radius:9px;padding:8px 12px;margin-bottom:16px}small{color:var(--muted)}
</style></head><body><main>
<h1>Divinci Table</h1><p class=lead>Play Commander with people and AI players, each with your own physical cards on camera.</p>
${error ? `<div class=err>${esc(error)}</div>` : ""}
<section><h2>Open tables</h2><ul>${list}</ul></section>
<section><h2>Start a table</h2><form method=post action="/lobby/rooms">
<label for=title>Table name</label><input type=text id=title name=title maxlength=40 placeholder="Friday Commander">
<label for=humans>Human seats (comma-separated names)</label><input type=text id=humans name=humans required placeholder="Michael, Sam">
<div class=ai><label style="font-weight:600">AI opponents (played by Divinci Fusion, up to two)</label>
<label><input type=checkbox name=ai value=tuvasa> Tuvasa the Sunlit — enchantments</label>
<label><input type=checkbox name=ai value=kaust> Kaust, Eyes of the Glade — face-down creatures</label>
<label><input type=checkbox name=ai value=ellivere> Ellivere of the Wild Court — Roles and Auras</label></div>
<button>Start table</button><br><small>Tables are public and listed here for six hours. Anyone with the link can claim an open seat.</small>
</form></section></main></body></html>`;
}
