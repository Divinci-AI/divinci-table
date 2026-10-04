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
	ADMIN_TOKEN?: string;
	ROOM_SNAPSHOTS: R2Bucket;
	ASSETS_BUCKET: R2Bucket;
	FUSION_CONFIG?: string;
	DND_CLOUD_AI?: string;          // "1" = AI Dungeon Master in public rooms (a budget decision; unset = a person DMs)
	DND_DM_RELEASE_ID?: string;
}

interface AiSeat { name: string; commander: string; deck: string }
interface RoomConfig { humans: string[]; ai: AiSeat[]; game: "magic" | "chess" | "dnd"; white?: string; black?: string; minutes?: number; increment?: number; dm?: string }
interface RoomInfo { id: string; title: string; game: string; humans: string[]; ai: string[]; created: number; claimed?: string[] }

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

	// Persistence: the container's disk dies with it, so the game lives in R2 between wakes. The table
	// server exports/imports its state on /api/room/* with a per-room token only this object knows.
	private needsRestore = false;      // set when a fresh container starts; cleared once the snapshot is back in
	private savedRev = "";             // the event id of the last snapshot written to R2 (for the record)
	private savedDigest = "";          // sha256 of the last snapshot written, so an unchanged game isn't rewritten
	private saving: Promise<void> | null = null;
	private lastSave = 0;

	override onStart(): void {
		this.needsRestore = true;
	}

	/** Save the game before the container goes to sleep, then let it sleep. */
	override async onActivityExpired(): Promise<void> {
		await this.save("sleep").catch((e) => console.error("save before sleep failed", e));
		await this.stop();
	}

	private async room(): Promise<{ id: string; token: string; config: RoomConfig } | null> {
		const config = await this.ctx.storage.get<RoomConfig>("config");
		if (!config) return null;
		let token = await this.ctx.storage.get<string>("token");
		if (!token) {
			token = [...crypto.getRandomValues(new Uint8Array(24))].map((b) => b.toString(16).padStart(2, "0")).join("");
			await this.ctx.storage.put("token", token);
		}
		return { id: (await this.ctx.storage.get<string>("id")) ?? "unknown", token, config };
	}

	private key(id: string) { return `rooms/${id}/snapshot.pkl`; }

	/** Pull the game out of the container and keep it in R2 (skipped when nothing changed). */
	private async save(why: string): Promise<void> {
		const r = await this.room();
		if (!r || !this.ctx.container?.running) return;
		const res = await this.containerFetch(new Request("http://room/api/room/snapshot", { headers: { "X-Room-Token": r.token } }), this.defaultPort);
		const rev = res.headers.get("X-Snapshot-Rev") ?? "";
		if (res.status !== 200) { console.error("room snapshot", res.status); await res.body?.cancel(); return; }
		const blob = await res.arrayBuffer();
		// The event counter doesn't move for every change (seat claims, high rolls), so compare the bytes.
		const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", blob))].map((b) => b.toString(16).padStart(2, "0")).join("");
		if (digest === this.savedDigest) return;
		await this.env.ROOM_SNAPSHOTS.put(this.key(r.id), blob, { customMetadata: { rev, why, saved: new Date().toISOString() } });
		this.savedRev = rev;
		this.savedDigest = digest;
		this.lastSave = Date.now();
	}

	/** At most one save every ~5 s while people play (R2 writes are cheap; a rollout can replace the container any time). A change that lands while a save is running marks
	 *  the room dirty, and another save follows, so the last change is never left only in the container
	 *  (a rollout or crash replaces it without the pre-sleep save). */
	private dirty = false;
	private saveSoon(): void {
		if (this.saving) { this.dirty = true; return; }
		this.dirty = false;
		const wait = Math.max(0, 5_000 - (Date.now() - this.lastSave));
		this.saving = new Promise<void>((done) => setTimeout(done, wait))
			.then(() => this.save("play"))
			.catch((e) => console.error("room save failed", e))
			.finally(() => { this.saving = null; if (this.dirty) this.saveSoon(); });
		this.ctx.waitUntil(this.saving);
	}

	/** Start (or reuse) the room's container with its environment, and fill it from R2 if it is fresh.
	 *  Every path that talks to the container goes through here, so none can boot it without ROOM_TOKEN. */
	private async ensureRunning(r: { id: string; token: string; config: RoomConfig }): Promise<void> {
		this.envVars = {
			ROOM_CONFIG: JSON.stringify(r.config),
			ROOM_TOKEN: r.token,
			...(this.env.DIVINCI_FUSION_API_KEY ? { DIVINCI_FUSION_API_KEY: this.env.DIVINCI_FUSION_API_KEY } : {}),
			...(this.env.FUSION_CONFIG ? { FUSION_CONFIG: this.env.FUSION_CONFIG } : {}),
			...(r.config.game === "dnd" && aiDm(this.env) ? { DND_CLOUD_AI: "1", DND_DM_RELEASE_ID: this.env.DND_DM_RELEASE_ID! } : {}),
		};
		await this.startAndWaitForPorts(this.defaultPort);
		if (this.needsRestore) await this.restoreIfFresh(r);
	}

	/** Fill a freshly started container from R2 (or adopt it when nothing is saved yet). */
	private async restoreIfFresh(r: { id: string; token: string }): Promise<void> {
		this.needsRestore = false;
		// onStart can fire for a container that is already running the game, so ask the process first:
		// only a fresh one (adopted=false) is filled from R2. Anything else would rewind live play.
		const st = await this.containerFetch(new Request("http://room/api/room/status", { headers: { "X-Room-Token": r.token } }), this.defaultPort)
			.then((x) => (x.ok ? x.json<{ adopted?: boolean }>() : { adopted: true })).catch(() => ({ adopted: true }));
		const saved = st.adopted ? null : await this.env.ROOM_SNAPSHOTS.get(this.key(r.id));
		if (!st.adopted && !saved) {
			await this.containerFetch(new Request("http://room/api/room/adopt", { method: "POST", headers: { "X-Room-Token": r.token } }), this.defaultPort)
				.then((x) => x.body?.cancel()).catch(() => undefined);
		}
		if (saved) {
			const res = await this.containerFetch(new Request("http://room/api/room/restore", {
				method: "POST", headers: { "X-Room-Token": r.token, "Content-Type": "application/octet-stream" },
				body: await saved.arrayBuffer(),
			}), this.defaultPort);
			if (!res.ok) console.error("restore failed", res.status, (await res.text()).slice(0, 200));
			else { await res.body?.cancel(); this.savedRev = saved.customMetadata?.rev ?? ""; this.lastSave = Date.now(); }
		}
	}

	async fetch(request: Request): Promise<Response> {
		const url = new URL(request.url);
		if (url.pathname === "/__room/setup" && request.method === "POST") {
			await this.ctx.storage.put("config", await request.json());
			await this.ctx.storage.put("id", url.searchParams.get("id") ?? "unknown");
			return new Response("ok");
		}
		const r = await this.room();
		if (!r) return new Response("This room doesn't exist (or has expired).", { status: 404 });
		if (url.pathname === "/__room/release") {                  // admin: free a seat whose device is lost
			await this.ensureRunning(r);
			const res = await this.containerFetch(new Request("http://room/api/room/release", {
				method: "POST", headers: { "X-Room-Token": r.token, "Content-Type": "application/json" },
				body: JSON.stringify({ seat: url.searchParams.get("seat") ?? "" }) }), this.defaultPort);
			this.saveSoon();
			if (res.ok) await this.env.LOBBY.get(this.env.LOBBY.idFromName("lobby")).markReleased(r.id, url.searchParams.get("seat") ?? "");
			return res;
		}
		if (url.pathname === "/__room/sleep") {                    // admin: save now, then stop the container
			await this.save("admin-sleep");
			if (this.ctx.container?.running) await this.stop();
			return Response.json({ slept: true, rev: this.savedRev });
		}
		await this.ensureRunning(r);
		// A rollout or crash can drop the container mid-request ("Container suddenly disconnected"). Start a
		// fresh one (which restores the saved game) and retry once, so the player never sees the swap.
		const body = request.method === "GET" || request.method === "HEAD" ? null : await request.arrayBuffer();
		const send = () => this.containerFetch(new Request(request.url, { method: request.method, headers: request.headers, body }), this.defaultPort);
		let res = await send().catch((e) => e as Error);
		if (res instanceof Error || res.status === 500 && (await res.clone().text()).includes("disconnected")) {
			console.error("container dropped mid-request; retrying once", res instanceof Error ? res.message : res.status);
			try {
				await this.ensureRunning(r);
				res = await send();
			} catch (e) {
				console.error("room unavailable after retry", (e as Error).message);
				return Response.json({ error: "The table is restarting. Try again in a few seconds." }, { status: 503, headers: { "Retry-After": "5" } });
			}
		}
		if (request.method !== "GET" && request.method !== "HEAD") this.saveSoon();
		if (url.pathname === "/api/seat/claim" && res.ok) {               // keep the lobby's open-seat count current
			const name = await res.clone().json<{ name?: string }>().then((d) => d.name ?? "").catch(() => "");
			if (name && r.id !== "unknown") {
				const lobby = this.env.LOBBY.get(this.env.LOBBY.idFromName("lobby"));
				this.ctx.waitUntil(lobby.markClaimed(r.id, name).catch(() => undefined));
			}
		}
		return res;
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

	/** A person claimed a human seat (seen by the room as the claim passes through). */
	async markClaimed(id: string, name: string): Promise<void> {
		const rooms = await this.list();
		const r = rooms.find((x) => x.id === id);
		if (!r || !r.humans.includes(name) || (r.claimed ?? []).includes(name)) return;
		r.claimed = [...(r.claimed ?? []), name];
		await this.ctx.storage.put("rooms", rooms);
	}

	/** A seat was released (admin): it shows as open again. */
	async markReleased(id: string, name: string): Promise<void> {
		const rooms = await this.list();
		const r = rooms.find((x) => x.id === id);
		if (!r) return;
		r.claimed = (r.claimed ?? []).filter((n) => n.toLowerCase() !== name.toLowerCase());
		await this.ctx.storage.put("rooms", rooms);
	}

	/** Take a room off the public list (admin). */
	async remove(id: string): Promise<boolean> {
		const rooms = await this.list();
		const left = rooms.filter((r) => r.id !== id);
		await this.ctx.storage.put("rooms", left);
		return left.length !== rooms.length;
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
		if (url.pathname === "/lobby/admin/release" && request.method === "POST") {
			if (!env.ADMIN_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`) return new Response("Forbidden", { status: 403 });
			const id = url.searchParams.get("id") ?? "";
			if (!/^[a-z0-9]{8}$/.test(id)) return new Response("bad id", { status: 400 });
			return env.ROOM.get(env.ROOM.idFromName(id)).fetch(new Request("https://room/__room/release?seat=" + encodeURIComponent(url.searchParams.get("seat") ?? "")));
		}
		if (url.pathname === "/lobby/admin/sleep" && request.method === "POST") {
			if (!env.ADMIN_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`) return new Response("Forbidden", { status: 403 });
			const id = url.searchParams.get("id") ?? "";
			if (!/^[a-z0-9]{8}$/.test(id)) return new Response("bad id", { status: 400 });
			return env.ROOM.get(env.ROOM.idFromName(id)).fetch(new Request("https://room/__room/sleep"));
		}
		if (url.pathname === "/lobby/admin/remove" && request.method === "POST") {
			const auth = request.headers.get("Authorization") ?? "";
			if (!env.ADMIN_TOKEN || auth !== `Bearer ${env.ADMIN_TOKEN}`) return new Response("Forbidden", { status: 403 });
			const id = url.searchParams.get("id") ?? "";
			return Response.json({ removed: await lobby.remove(id) });
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
		if (url.pathname.startsWith("/__room") || url.pathname.startsWith("/api/room/")) return new Response("Not found", { status: 404 });
		if (url.pathname.startsWith("/avatars/") && request.method === "GET") return avatar(url.pathname, env);

		const room = (request.headers.get("Cookie") ?? "").match(/(?:^|;\s*)room=([a-z0-9]{8})/)?.[1];
		if (!room) return Response.redirect(url.origin + "/", 302);
		const headers = new Headers(request.headers);
		for (const h of ["X-Forwarded-For", "Forwarded", "X-Real-IP", "True-Client-IP"]) headers.delete(h);
		headers.set("X-Forwarded-For", request.headers.get("CF-Connecting-IP") ?? "unknown");   // always "remote"
		return env.ROOM.get(env.ROOM.idFromName(room)).fetch(new Request(request, { headers }));
	},
} satisfies ExportedHandler<Env>;

/** The stage's 3D avatars, shared by every room: R2 avatars/<name>.glb|.png, plus an index of model names. */
async function avatar(path: string, env: Env): Promise<Response> {
	if (path === "/avatars/index.json") {
		const listed = await env.ASSETS_BUCKET.list({ prefix: "avatars/" });
		const names = listed.objects.map((o) => o.key.slice(8)).filter((k) => k.endsWith(".glb")).map((k) => k.slice(0, -4));
		return Response.json(names.sort(), { headers: { "Cache-Control": "public, max-age=300" } });
	}
	const name = decodeURIComponent(path.slice(9));
	if (!/^[A-Za-z0-9 ._-]{1,80}\.(glb|png)$/.test(name)) return new Response("Not found", { status: 404 });
	const obj = await env.ASSETS_BUCKET.get("avatars/" + name);
	if (!obj) return new Response("Not found", { status: 404 });
	return new Response(obj.body, { headers: {
		"Content-Type": obj.httpMetadata?.contentType ?? (name.endsWith(".png") ? "image/png" : "model/gltf-binary"),
		"Cache-Control": "public, max-age=86400", "ETag": obj.httpEtag } });
}

async function createRoom(request: Request, env: Env, lobby: DurableObjectStub<Lobby>): Promise<Response> {
	const form = await request.formData();
	const back = (msg: string) => Response.redirect(new URL("/?error=" + encodeURIComponent(msg), request.url).toString(), 303);
	const g = String(form.get("game") ?? "");
	const game = g === "chess" || g === "dnd" ? g : "magic";
	const humans = String(form.get("humans") ?? "").split(",").map((s) => s.trim()).filter(Boolean);
	if (game === "chess") return createChess(request, env, lobby, form, humans, back);
	if (game === "dnd") return createDnd(request, env, lobby, form, humans, back);
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
	await env.ROOM.get(env.ROOM.idFromName(id)).fetch(new Request("https://room/__room/setup?id=" + id, {
		method: "POST", body: JSON.stringify({ humans, ai, game: "magic" } satisfies RoomConfig),
	}));
	return Response.redirect(new URL("/r/" + id, request.url).toString(), 303);
}

/** A chess table: one or two people; with one, Leonardo (Stockfish) takes the black pieces. */
async function createChess(request: Request, env: Env, lobby: DurableObjectStub<Lobby>, form: FormData, humans: string[],
	back: (msg: string) => Response): Promise<Response> {
	if (humans.length < 1 || humans.length > 2) return back("Chess seats one or two people (with one, Leonardo plays the other side).");
	if (!humans.every((h) => NAME_RE.test(h) && !h.includes(":"))) return back("Seat names: letters, numbers and spaces, up to 24 characters.");
	const level = Math.max(1, Math.min(20, Number(form.get("level")) || 6));
	const minutes = [5, 10, 15, 30].includes(Number(form.get("minutes"))) ? Number(form.get("minutes")) : 15;
	const white = humans[0], black = humans[1] ?? `ai:Leonardo:${level}`;
	const title = String(form.get("title") ?? "").trim().slice(0, 40) || `${humans[0]}'s chess board`;
	const id = [...crypto.getRandomValues(new Uint8Array(8))].map((b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");
	const why = await lobby.add({ id, title, game: "chess", humans, ai: humans[1] ? [] : [`Leonardo (level ${level})`], created: Date.now() },
		request.headers.get("CF-Connecting-IP") ?? "unknown");
	if (why) return back(why);
	await env.ROOM.get(env.ROOM.idFromName(id)).fetch(new Request("https://room/__room/setup?id=" + id, {
		method: "POST", body: JSON.stringify({ game: "chess", humans, ai: [], white, black, minutes, increment: minutes >= 15 ? 10 : 5 } satisfies RoomConfig),
	}));
	return Response.redirect(new URL("/r/" + id, request.url).toString(), 303);
}

const aiDm = (env: Env) => env.DND_CLOUD_AI === "1" && !!env.DND_DM_RELEASE_ID && !!env.DIVINCI_FUSION_API_KEY;

/** A D&D one-shot: up to six players. Unless the AI Dungeon Master is switched on for public rooms
 *  (a budget decision), the FIRST name runs the game from behind the screen. */
async function createDnd(request: Request, env: Env, lobby: DurableObjectStub<Lobby>, form: FormData, humans: string[],
	back: (msg: string) => Response): Promise<Response> {
	const ai = aiDm(env);
	if (humans.length < (ai ? 1 : 2) || humans.length > 6) {
		return back(ai ? "D&D seats one to six players." : "D&D seats two to seven people: the first name is the Dungeon Master.");
	}
	if (!humans.every((h) => NAME_RE.test(h) && !h.includes(":") && !h.includes(","))) return back("Seat names: letters, numbers and spaces, up to 24 characters.");
	const dm = ai ? undefined : humans[0];
	const players = ai ? humans : humans.slice(1);
	const title = String(form.get("title") ?? "").trim().slice(0, 40) || `${humans[0]}'s adventure`;
	const id = [...crypto.getRandomValues(new Uint8Array(8))].map((b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");
	const why = await lobby.add({ id, title, game: "dnd", humans, ai: ai ? ["AI Dungeon Master"] : [], created: Date.now() },
		request.headers.get("CF-Connecting-IP") ?? "unknown");
	if (why) return back(why);
	await env.ROOM.get(env.ROOM.idFromName(id)).fetch(new Request("https://room/__room/setup?id=" + id, {
		method: "POST", body: JSON.stringify({ game: "dnd", humans: players, ai: [], dm } satisfies RoomConfig),
	}));
	return Response.redirect(new URL("/r/" + id, request.url).toString(), 303);
}

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const html = (body: string) => new Response(body, { headers: { "Content-Type": "text/html; charset=utf-8" } });

const SIGIL = `<svg class="sigil" viewBox="0 0 100 100" aria-hidden="true"><defs>
<radialGradient id="glow"><stop offset="0" stop-color="#7ff3ea" stop-opacity=".9"/><stop offset=".45" stop-color="#3fd0c9" stop-opacity=".35"/><stop offset="1" stop-color="#3fd0c9" stop-opacity="0"/></radialGradient>
<linearGradient id="gold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f4dc9b"/><stop offset="1" stop-color="#8a6a2e"/></linearGradient></defs>
<circle cx="50" cy="50" r="46" fill="none" stroke="url(#gold)" stroke-width="2"/><circle cx="50" cy="50" r="38" fill="none" stroke="#3fd0c9" stroke-width="1" stroke-dasharray="2 4"/>
<path d="M50 14l30 18v36L50 86 20 68V32z" fill="none" stroke="url(#gold)" stroke-width="2"/>
<path d="M50 14v22M50 64v22M20 32l19 11M61 57l19 11M80 32L61 43M39 57L20 68" stroke="url(#gold)" stroke-width="1.5"/>
<circle class="halo" cx="50" cy="50" r="22" fill="url(#glow)"/><circle class="ring" cx="50" cy="50" r="15" fill="none" stroke="#3fd0c9" stroke-width="1.5"/><circle class="core" cx="50" cy="50" r="9" fill="#7ff3ea"/></svg>`;

function lobbyPage(rooms: RoomInfo[], error: string): string {
	const list = rooms.length
		? rooms.map((r) => {
			const open = r.humans.filter((h) => !(r.claimed ?? []).includes(h));
			const seats = open.length ? `${open.length} seat${open.length > 1 ? "s" : ""} open: ${esc(open.join(", "))}` : "Full";
			return `<li><a href="/r/${r.id}"><img src="/brand/${r.game === "chess" ? "game-chess" : r.game === "dnd" ? "quantum-dice" : "game-magic"}.jpg" alt=""><span class="t">${esc(r.title)}</span>
			<span class="m">${r.game === "chess" ? "Chess" : r.game === "dnd" ? "D&amp;D one-shot" : "Commander"} · ${seats}${r.ai.length ? " · AI: " + esc(r.ai.join(", ")) : ""} · ${Math.max(1, Math.round((Date.now() - r.created) / 60000))} min ago</span>
			<span class="go">${open.length ? "Take a seat →" : "Watch →"}</span></a></li>`; }).join("")
		: `<li class="empty">No open tables yet. Start the first one.</li>`;
	return `<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Divinci Table</title><meta name=description content="Play Commander with people and AI players, each with your own physical cards on camera.">
<link rel=preconnect href="https://fonts.googleapis.com"><link rel=preconnect href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700;900&family=Cormorant+Garamond:ital,wght@0,400;0,600;1,400&display=swap" rel=stylesheet>
<style>
:root{--abyss:#040d12;--deep:#0b2430;--teal:#3fd0c9;--gold:#d9b46a;--gold-hi:#f4dc9b;--gold-lo:#8a6a2e;--parch:#efe3c6;--text:#e9e2cf;--muted:#a9b5b4;--ink:#2a1f12}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;color:var(--text);font:20px/1.5 "Cormorant Garamond",Georgia,serif;
  background:radial-gradient(1100px 650px at 50% -10%,#16465a 0%,transparent 60%),radial-gradient(800px 500px at 85% 45%,rgba(63,208,201,.08),transparent 60%),
  linear-gradient(180deg,var(--deep),var(--abyss) 60%)}
main{max-width:920px;margin:0 auto;padding:44px 16px 60px}
h1,h2,.cz{font-family:Cinzel,Georgia,serif;letter-spacing:.04em}
header{text-align:center;margin-bottom:34px}
.sigil{width:84px;height:84px;filter:drop-shadow(0 0 16px rgba(63,208,201,.6))}
.sigil .core,.sigil .ring,.sigil .halo{transform-box:fill-box;transform-origin:center}
.sigil .core{animation:core 2.6s ease-in-out infinite;filter:drop-shadow(0 0 6px #7ff3ea)}
.sigil .ring{animation:ring 2.6s ease-out infinite}.sigil .halo{animation:halo 2.6s ease-in-out infinite}
@keyframes core{0%,100%{transform:scale(.88);opacity:.8}50%{transform:scale(1.12);opacity:1}}
@keyframes ring{0%{transform:scale(.9);opacity:.9}70%,100%{transform:scale(1.45);opacity:0}}
@keyframes halo{0%,100%{transform:scale(.8);opacity:.45}50%{transform:scale(1.25);opacity:1}}
.kicker{font:600 13px Cinzel,serif;letter-spacing:.32em;text-transform:uppercase;color:var(--teal);margin-top:10px}
h1{margin:6px 0 6px;font-size:clamp(42px,8vw,72px);font-weight:900;line-height:1.05;
  background:linear-gradient(180deg,var(--gold-hi),var(--gold) 45%,var(--gold-lo));-webkit-background-clip:text;background-clip:text;color:transparent}
header p{max-width:620px;margin:0 auto;color:var(--parch)}
section{position:relative;margin:0 0 28px;padding:24px clamp(16px,4vw,32px);border-radius:12px;
  background:linear-gradient(180deg,#1b3440,#0d1d25);box-shadow:inset 0 0 0 1px rgba(217,180,106,.5),inset 0 0 0 5px rgba(8,22,29,.9),inset 0 0 0 6px rgba(217,180,106,.25),0 18px 44px rgba(0,0,0,.45)}
h2{margin:0 0 14px;font-size:22px;color:var(--gold-hi)}
ul{list-style:none;margin:0;padding:0;display:grid;gap:12px}
li a{display:grid;grid-template-columns:64px 1fr auto;grid-template-rows:auto auto;column-gap:14px;align-items:center;padding:10px;border-radius:8px;text-decoration:none;
  background:rgba(4,13,18,.55);box-shadow:inset 0 0 0 1px rgba(217,180,106,.25);transition:box-shadow .2s,transform .2s}
li a:hover{transform:translateY(-2px);box-shadow:inset 0 0 0 1px var(--gold),0 0 18px rgba(63,208,201,.2)}
li img{grid-row:1/3;width:64px;height:64px;border-radius:6px;object-fit:cover;box-shadow:0 0 0 1px var(--gold-lo)}
li .t{font:700 17px Cinzel,serif;color:var(--parch)}li .m{grid-column:2;color:var(--muted);font-size:16px}
li .go{grid-row:1/3;grid-column:3;font:600 13px Cinzel,serif;letter-spacing:.08em;color:var(--teal)}
li.empty{color:var(--muted);padding:6px 2px}
label{display:block;margin:14px 0 6px;font:600 13px Cinzel,serif;letter-spacing:.12em;color:var(--gold-hi);text-transform:uppercase}
input[type=text]{width:100%;padding:12px 14px;border-radius:4px;border:1px solid rgba(217,180,106,.5);background:#071820;color:var(--parch);
  font:500 19px "Cormorant Garamond",Georgia,serif;box-shadow:inset 0 2px 8px rgba(0,0,0,.6)}
input[type=text]:focus{outline:2px solid var(--teal);outline-offset:2px}
.opps{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.opp{position:relative;display:block;margin:0;padding:12px 12px 12px 40px;border-radius:6px;cursor:pointer;text-transform:none;letter-spacing:0;
  font:400 17px "Cormorant Garamond",Georgia,serif;color:var(--parch);background:rgba(4,13,18,.6);box-shadow:inset 0 0 0 1px rgba(217,180,106,.3)}
.opp b{display:block;font:700 15px Cinzel,serif;color:var(--gold-hi)}
.opp input{position:absolute;left:14px;top:16px;accent-color:#3fd0c9;width:16px;height:16px}
.opp:has(input:checked){box-shadow:inset 0 0 0 1px var(--teal),0 0 14px rgba(63,208,201,.3)}
button{margin:20px 0 6px;padding:14px 28px;border:1px solid var(--gold);border-radius:4px;cursor:pointer;font:700 16px Cinzel,serif;letter-spacing:.08em;color:#1a1206;
  background:linear-gradient(180deg,var(--gold-hi),var(--gold) 55%,var(--gold-lo));box-shadow:inset 0 1px 0 rgba(255,255,255,.5),0 0 0 3px rgba(11,36,48,.9),0 0 0 4px var(--gold-lo),0 8px 22px rgba(0,0,0,.45)}
button:hover{filter:brightness(1.07)}
small{color:var(--muted);font-size:16px;display:block}
.err{margin:0 0 20px;padding:10px 14px;border-radius:4px;background:rgba(224,136,74,.12);border:1px solid rgba(224,136,74,.6);color:#ffd9bf}
.how{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:0 0 28px}
.how figure{margin:0;border-radius:8px;overflow:hidden;background:#0d1d25;box-shadow:0 0 0 1px rgba(217,180,106,.35)}
.how img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover}
.how figcaption{padding:8px 12px 10px;font-size:16px;color:var(--parch)}
.how figcaption b{display:block;font:700 13px Cinzel,serif;letter-spacing:.06em;color:var(--gold-hi)}
footer{text-align:center;color:#7c8b8d;font-size:15px}footer a{color:var(--gold)}
@media (max-width:700px){.how{grid-template-columns:1fr}li a{grid-template-columns:52px 1fr}li img{width:52px;height:52px}li .go{display:none}}
@media (prefers-reduced-motion:reduce){.sigil *{animation:none!important}}
</style></head><body><main>
<header>${SIGIL}<div class="kicker">A new kind of game night</div><h1>Divinci Table</h1>
<p>Commander with people and AI players. Everyone keeps their own physical cards on camera; phones pass priority, track life and share photos.</p></header>
${error ? `<div class="err">${esc(error)}</div>` : ""}
<p style="text-align:center"><a href="/leaderboard" style="color:var(--gold-hi)">Results so far: which AIs won, and the caveats →</a></p>
<section><h2>Open tables</h2><ul>${list}</ul></section>
<section><h2>Start a table</h2><form method=post action="/lobby/rooms">
<label>Game</label><div class="opps">
<label class="opp"><input type=radio name=game value=magic checked><b>Magic: Commander</b>Two to four seats, people and AI</label>
<label class="opp"><input type=radio name=game value=chess><b>Chess</b>One or two people; alone, you face Leonardo</label>
<label class="opp"><input type=radio name=game value=dnd><b>D&amp;D one-shot</b>Real dice or fair ones; the first name is the Dungeon Master</label></div>
<label for=title>Table name</label><input type=text id=title name=title maxlength=40 placeholder="Friday Commander">
<label for=humans>Human seats (comma-separated names; chess: White first)</label><input type=text id=humans name=humans required placeholder="Michael, Sam">
<label>AI opponents · played by Divinci Fusion · up to two</label>
<div class="opps">
<label class="opp"><input type=checkbox name=ai value=tuvasa><b>Tuvasa the Sunlit</b>Enchantments that grow</label>
<label class="opp"><input type=checkbox name=ai value=kaust><b>Kaust, Eyes of the Glade</b>Face-down surprises</label>
<label class="opp"><input type=checkbox name=ai value=ellivere><b>Ellivere of the Wild Court</b>Roles and Auras</label></div>
<details class="chessopts"><summary style="cursor:pointer;color:var(--gold-hi);margin-top:12px">Chess options</summary>
<label for=minutes>Time per player</label><select id=minutes name=minutes style="padding:8px;border-radius:4px;background:#071820;color:var(--parch);border:1px solid rgba(217,180,106,.5)">
<option value=5>5 minutes</option><option value=10>10 minutes</option><option value=15 selected>15 minutes</option><option value=30>30 minutes</option></select>
<label for=level>Leonardo's strength (1–20)</label><input type=text id=level name=level value=6 inputmode=numeric style="max-width:90px">
</details>
<button>Start table</button><small>Tables are public and listed here for six hours. Anyone with the link can claim an open seat and watch the game. Photos you share are visible to everyone in the room.</small>
</form></section>
<div class="how">
<figure><img src="/brand/your-cards.jpg" alt="Painted cards on a candlelit table under a brass camera arm." loading=lazy><figcaption><b>Your cards, your table</b>Play your own deck; a phone over your play area shows it.</figcaption></figure>
<figure><img src="/brand/ai-opponent.jpg" alt="A brass automaton holding a hand of cards." loading=lazy><figcaption><b>Opponents with a soul</b>AI players with personalities and voices.</figcaption></figure>
<figure><img src="/brand/quantum-dice.jpg" alt="A crystal d20 rising from a sunken city." loading=lazy><figcaption><b>Dice from the deep</b>Shuffles and rolls anyone can verify.</figcaption></figure>
</div>
<footer>Part of <a href="https://divinci.ai/">Divinci</a>. Not affiliated with Wizards of the Coast.</footer>
</main></body></html>`;
}
