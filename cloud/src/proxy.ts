/**
 * A visitor's request to a room, sent on from the Worker.
 *
 * With a room host (HostedRoom) the Worker talks to the room host directly: one stateless hop, held open for as long as
 * the table holds it (a page's long-poll is twenty seconds) at no cost, because a Worker is billed for the time it
 * computes, not the time it waits. The room's Durable Object is asked only when the room host says the room is asleep;
 * it decides whether the room still exists and wakes it. With container rooms (TableRoom) the object IS the room, and
 * everything goes through it as before.
 *
 * Also here: the limits on that traffic. Per address and per seat key (the Rate Limiting bindings, when configured), and
 * a daily budget for the agent API, counted by sampling so that counting costs one Durable Object call per fifty
 * requests.
 */
import { budgetRefusal, dailyBudget, REQUEST_SAMPLE } from "./policy";
import { drain, hosted, hostFetch, lobbyOf, PRIVATE_HEADERS, restarting } from "./shared";
import type { Env } from "./shared";

const slow = (what: string) => Response.json({ error: `Slow down: too many requests ${what}. Wait a few seconds.`, retry_after: 5 }, { status: 429, headers: { "Retry-After": "5" } });

// What this isolate last heard about today's budget. An isolate that has heard nothing lets requests through and
// learns within a few dozen of them; isolates are short-lived, so this is a cache, never the count itself.
const BUDGET = { day: "", over: false, addrs: new Set<string>() };

/** The limits on room and agent-API traffic: a refusal, or null to go on. Reads the request body only to drop it.
 *  The daily budget is the AGENT API's alone (`agents`): however busy agents are, a people's table is never told to rest. */
export async function limited(request: Request, env: Env, ctx: ExecutionContext, addr: string, agents: boolean): Promise<Response | null> {
	const seat = request.headers.get("X-Seat-Key");
	const [a, s] = await Promise.all([
		env.PER_ADDRESS ? env.PER_ADDRESS.limit({ key: addr }).catch(() => ({ success: true })) : { success: true },
		seat && env.PER_SEAT ? env.PER_SEAT.limit({ key: seat.slice(0, 80) }).catch(() => ({ success: true })) : { success: true },
	]);
	if (!a.success || !s.success) { await drain(request); return slow(!s.success ? "for this seat" : "from this address"); }
	if (!agents) return null;
	// The budget counts only requests that passed the limits above (a refused flood must not spend everyone's day), and no
	// address may spend more than a quarter of it: past that, only that address rests.
	const day = new Date().toISOString().slice(0, 10);
	if (BUDGET.day !== day) { BUDGET.day = day; BUDGET.over = false; BUDGET.addrs.clear(); }
	if (BUDGET.over || BUDGET.addrs.has(addr)) { await drain(request); return budgetRefusal(); }
	if (Math.random() < 1 / REQUEST_SAMPLE) {
		ctx.waitUntil(lobbyOf(env).countRequests(REQUEST_SAMPLE, dailyBudget(env.DAILY_REQUEST_BUDGET), addr)
			.then((t) => { if (t.day === BUDGET.day) { BUDGET.over = t.over; if (t.addrOver && BUDGET.addrs.size < 1000) BUDGET.addrs.add(addr); } }).catch(() => undefined));
	}
	return null;
}

/** Send `request` to room `id` as `pathq` (a path, with its query). `addr` is the visitor's address. */
export async function roomRequest(request: Request, env: Env, ctx: ExecutionContext, id: string, pathq: string, addr: string): Promise<Response> {
	const path = pathq.split("?")[0];
	// the private doors: the Worker's to a room (/__room), a room host's own (/__host, /__r), a room's to its lobby (/api/room)
	if (!path.startsWith("/") || path.startsWith("//") || /^\/__(room|host|r)(\/|$)/.test(path) || path.startsWith("/api/room/")) {
		await drain(request);
		return new Response("Not found", { status: 404 });
	}
	const headers = new Headers(request.headers);
	const roomToken = path === "/api/xr/judge" ? request.headers.get("X-Room-Token") : null;   // the room's own process asking its judge: the object checks it
	for (const h of [...PRIVATE_HEADERS, "X-Forwarded-For", "Forwarded", "X-Real-IP", "True-Client-IP"]) headers.delete(h);
	if (roomToken) headers.set("X-Room-Token", roomToken);
	headers.set("X-Forwarded-For", addr);                        // always "remote": never the table's own laptop
	const room = env.ROOM.get(env.ROOM.idFromName(id));
	const origin = new URL(request.url).origin;
	if (!hosted(env) || path.startsWith("/api/xr/")) {           // container rooms; and what a hosted room keeps in its object (presence, voice, the judge)
		return room.fetch(new Request(origin + pathq, { method: request.method, headers,       // (origin + path, never new URL(path, origin): "//x/…" would name a host)
			body: request.method === "GET" || request.method === "HEAD" ? null : request.body }));
	}
	headers.set("X-Client-Addr", addr.slice(0, 100));
	headers.set("X-Public-Host", new URL(env.PUBLIC_ORIGIN || origin).host);
	const body = request.method === "GET" || request.method === "HEAD" ? null : await request.arrayBuffer();
	const send = () => hostFetch(env, `/__r/${id}${pathq}`, { method: request.method, headers, body }).catch((e) => e as Error);
	let res = await send();
	if (res instanceof Error || res.headers.has("X-Room-Down")) {  // asleep, restarted, or gone: the room's object knows which
		// "not running" is the room host itself saying the request went nowhere. Anything else (a dropped connection, no
		// answer) may have reached the table: a move is then never sent a second time, only a read is.
		const untouched = !(res instanceof Error) && res.headers.get("X-Room-Down") === "not running";
		if (!(res instanceof Error)) await res.body?.cancel();
		const woke = await room.fetch(new Request("https://room/__room/ensure", { method: "POST" })).catch(() => null);
		const status = woke?.status ?? 503;
		await woke?.body?.cancel();
		if (status === 404) return new Response("This room doesn't exist (or has expired).", { status: 404 });
		if (status !== 200 || !(untouched || request.method === "GET" || request.method === "HEAD")) return restarting();
		res = await send();
		if (res instanceof Error || res.headers.has("X-Room-Down")) {
			if (!(res instanceof Error)) await res.body?.cancel();
			return restarting();
		}
	}
	if (path === "/api/seat/claim" && request.method === "POST" && res.ok) {      // keep the lobby's open-seat count current
		const name = await res.clone().json<{ name?: string }>().then((d) => d.name ?? "").catch(() => "");
		if (name) ctx.waitUntil(lobbyOf(env).markClaimed(id, name).catch(() => undefined));
	}
	return res;
}
