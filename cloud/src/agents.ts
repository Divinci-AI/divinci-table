/**
 * The agent API: how an outside AI player finds a table and sits down with its own key (docs/agents.md, served at
 * /agents/skill.md).
 *
 *   POST /agents/register                {"name"}                 -> {"agent": {id, name}, "key": "dta_…"}   (shown once)
 *   GET  /agents/me                      Bearer                   -> the agent and the seats it holds
 *   GET  /agents/tables                                           -> open agent tables and their seats
 *   POST /agents/tables                  Bearer {seats, decks, title} -> a new table (2–4 virtual-deck seats)
 *   POST /agents/tables/<id>/claim       Bearer {"seat"}?         -> {"seat", "seat_key", "api"}; the last claim starts the game
 *   ANY  /agents/tables/<id>/api/…       X-Seat-Key               -> that table's own API (the same one a person's phone uses)
 *   GET  /agents/results                                          -> how the last agent games ended
 *
 * Every limit is in policy.ts and counted in the Lobby object. Agent tables need a room host (HostedRoom): a seat is a
 * virtual deck, played over the seat API by the agent's own model, so nothing here spends the lobby's AI budget.
 */
import { AGENT_BODY_MAX, bearerAgentKey, isAdmin, planAgentTable } from "./policy";
import { limited, roomRequest } from "./proxy";
import { drain, hosted, lobbyOf, newRoomId, PILOT_DECKS, readCapped } from "./shared";
import type { Env, RoomConfig, RoomInfo } from "./shared";

const SEATS = ["North", "East", "South", "West"];
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
	Response.json(body, { status, headers: { "Cache-Control": "no-store", ...headers } });
const refuse = (r: { status: number; error: string; retryAfter?: number }) =>
	json({ error: r.error }, r.status, r.retryAfter ? { "Retry-After": String(r.retryAfter) } : {});

async function readJson(request: Request): Promise<Record<string, unknown> | null> {
	const text = await readCapped(request, AGENT_BODY_MAX);
	if (text === null) return null;
	if (!text.trim()) return {};
	try {
		const v = JSON.parse(text);
		return v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null;
	} catch { return null; }
}

function tableView(r: RoomInfo, origin: string) {
	const full = (r.claimed ?? []).length >= r.humans.length;
	return { id: r.id, title: r.title, game: "commander", created: new Date(r.created).toISOString(), status: r.over ? "over" : full ? "playing" : "waiting",
		seats: r.humans.map((s) => ({ seat: s, commander: r.decks?.[s] ?? null, open: !(r.claimed ?? []).includes(s), agent: r.by?.[s]?.name ?? null })),
		open_seats: r.humans.filter((s) => !(r.claimed ?? []).includes(s)).length,
		watch: `${origin}/r/${r.id}`, claim: `${origin}/agents/tables/${r.id}/claim`, api: `${origin}/agents/tables/${r.id}/api` };
}

/** Handles /agents/*; returns null for any other path. */
export async function agentApi(request: Request, env: Env, ctx: ExecutionContext, url: URL): Promise<Response | null> {
	const path = url.pathname;
	if (path !== "/agents" && !path.startsWith("/agents/")) return null;
	const origin = (env.PUBLIC_ORIGIN || url.origin).replace(/\/+$/, "");
	const lobby = lobbyOf(env);
	const addr = request.headers.get("CF-Connecting-IP") ?? "unknown";
	const hosting = hosted(env);

	if ((path === "/agents" || path === "/agents/") && request.method === "GET") return Response.redirect(origin + "/agents/skill.md", 302);
	const no = await limited(request, env, ctx, addr, true);
	if (no) return no;

	// a table's own API, by path instead of by cookie: /agents/tables/<id>/api/… → /api/… in that room
	const api = path.match(/^\/agents\/tables\/([a-z0-9]{8})(\/api\/.*)$/);
	if (api) {
		if (api[2].startsWith("/api/xr/")) { await drain(request); return json({ error: "not found" }, 404); }
		return roomRequest(request, env, ctx, api[1], api[2] + url.search, addr);
	}

	if (path === "/agents/results" && request.method === "GET") {
		return json({ results: (await lobby.results()).map((g) => ({ ...g, at: new Date(g.at).toISOString(), watch: `${origin}/r/${g.id}` })) }, 200, { "Cache-Control": "public, max-age=30" });
	}

	if (path === "/agents/tables" && request.method === "GET") {
		const tables = (await lobby.list()).filter((r) => r.agents).map((r) => tableView(r, origin));
		return json({ tables, open: tables.filter((t) => t.open_seats > 0).length, decks: Object.fromEntries(Object.entries(PILOT_DECKS).map(([k, d]) => [k, d.commander])),
			guide: origin + "/agents/skill.md", hosting }, 200, { "Cache-Control": "public, max-age=5" });
	}

	if (request.method !== "POST" && !(path === "/agents/me" && request.method === "GET")) {
		await drain(request);
		return json({ error: "not found" }, 404);
	}

	if (path === "/agents/register") {
		const b = await readJson(request);
		if (!b) return json({ error: "Send a small JSON object: {\"name\": \"…\"}." }, 400);
		const out = await lobby.registerAgent(String(b.name ?? "").trim(), addr, await isAdmin(request.headers.get("Authorization"), env.ADMIN_TOKEN));
		if ("error" in out) return refuse(out);
		return json({ agent: { id: out.id, name: out.name }, key: out.key,
			note: "Keep this key: it is shown once and cannot be recovered. Send it as 'Authorization: Bearer <key>'.", guide: origin + "/agents/skill.md" }, 201);
	}

	// everything below needs the agent's key. The (small) body is read first: a refusal must not leave it unread.
	const b = request.method === "POST" ? await readJson(request) : {};
	const key = bearerAgentKey(request.headers.get("Authorization"));
	const who = await lobby.agentByKey(key, addr);
	if ("error" in who) return refuse(who);

	if (path === "/agents/me") {
		const mine = (await lobby.list()).filter((r) => r.agents && Object.values(r.by ?? {}).some((a) => a.id === who.id));
		return json({ agent: { id: who.id, name: who.name }, tables: mine.map((r) => ({ ...tableView(r, origin),
			my_seats: Object.entries(r.by ?? {}).filter(([, a]) => a.id === who.id).map(([s]) => s) })) });
	}

	if (!hosting) return json({ error: "This lobby does not host agent tables." }, 501);

	if (path === "/agents/tables") {
		if (!b) return json({ error: "Send a small JSON object." }, 400);
		const names = Object.keys(PILOT_DECKS);
		const shuffled = names.map((n) => [crypto.getRandomValues(new Uint32Array(1))[0], n] as const).sort((x, y) => x[0] - y[0]).map((x) => x[1]);
		const plan = planAgentTable({ seats: b.seats, decks: b.decks, title: b.title }, names, shuffled, who.name);
		if (typeof plan === "string") return json({ error: plan }, 400);
		const seats = SEATS.slice(0, plan.seats);
		const pilots = seats.map((s, i) => ({ name: s, ...PILOT_DECKS[plan.decks[i]] }));
		const id = newRoomId();
		const info: RoomInfo = { id, title: plan.title, game: "magic", humans: seats, ai: [], created: Date.now(), agents: true,
			decks: Object.fromEntries(pilots.map((p) => [p.name, p.commander])), claimed: [], by: {} };
		const why = await lobby.addAgentTable(info, who.id, addr);
		if (why) return refuse(why);
		await env.ROOM.get(env.ROOM.idFromName(id)).fetch(new Request("https://room/__room/setup?id=" + id, {
			method: "POST", body: JSON.stringify({ humans: [], ai: [], pilots, game: "magic", agents: true } satisfies RoomConfig) }));
		return json({ table: tableView(info, origin), next: `POST ${origin}/agents/tables/${id}/claim to take a seat. The game starts when every seat is taken.` }, 201);
	}

	const claim = path.match(/^\/agents\/tables\/([a-z0-9]{8})\/claim$/);
	if (claim) {
		if (!b) return json({ error: "Send a small JSON object, or nothing." }, 400);
		const id = claim[1];
		const got = await lobby.reserveSeat(id, typeof b.seat === "string" ? b.seat : "", who);
		if ("error" in got) return refuse(got);
		const res = await env.ROOM.get(env.ROOM.idFromName(id)).fetch(new Request(
			`https://room/__room/agent-claim?seat=${encodeURIComponent(got.seat)}&agent=${encodeURIComponent(who.id)}`, { method: "POST" }));
		if (!res.ok) {
			if (got.fresh) await lobby.unreserveSeat(id, got.seat, who.id);
			const status = res.status === 409 ? 409 : 503;
			await res.body?.cancel();
			return json({ error: status === 409 ? "That seat is taken." : "The table could not be reached. Try again in a few seconds." }, status, status === 503 ? { "Retry-After": "5" } : {});
		}
		const d = await res.json<{ seat: string; key: string; started: boolean; waiting_for: string[] }>();
		const base = `${origin}/agents/tables/${id}/api`;
		return json({ table: id, seat: d.seat, commander: got.commander, seat_key: d.key, started: d.started, waiting_for: d.waiting_for, api: base, watch: `${origin}/r/${id}`,
			how: { header: "X-Seat-Key: <seat_key> on every call", what_can_i_do: `GET ${base}/seat/legal?seat=${d.seat}`, act: `POST ${base}/brain/<action> with the listed body`,
				my_cards: `GET ${base}/brain/state?seat=${d.seat}`, table_log: `GET ${base}/events?since=0&wait=20` },
			note: "Keep the seat key: it is this seat. Claim again with your agent key if you lose it." });
	}
	return json({ error: "not found" }, 404);
}
