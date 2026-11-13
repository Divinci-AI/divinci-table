/**
 * A table room that runs on a room host (cloud/roomhost.py on a machine you control) instead of in a
 * Cloudflare Container. Bound as ROOM by wrangler.host.jsonc. The Worker and this object stay the only public
 * address; the room host answers only requests carrying ROOM_HOST_KEY.
 *
 * What this object does: it knows the room (its config, its token, when it was made), wakes its process on the room
 * host, takes an agent's seat for it and starts an agent game, and holds what needs one place: presence sockets, the
 * Clef judge and the headset's voice/photo counters. /__room/* is the Worker's private door (never reachable from
 * outside).
 *
 * What it does NOT do: carry the room's ordinary traffic. The Worker sends that to the room host itself (proxy.ts) and
 * comes here only when the room is asleep. A Durable Object is billed for the time it is busy, and a table's pages hold
 * requests open for twenty seconds at a time, so one table passing through here would keep it busy all day.
 *
 * The room host keeps the saved game on its own disk, so nothing here needs R2, and it holds an agent table to its
 * rules (cloud/roomhost.py: agent_refusal).
 */
import { DurableObject } from "cloudflare:workers";
import { AGENT_OPEN_TABLE_TTL_MS, aiKeyAllowed, boardPrompt, parseBoardReply, parsePose, PRESENCE_MAX_BYTES, PRESENCE_MAX_PER_SEC, sameText, speechHint,
	STT_MAX_BYTES, STT_PER_10_MIN, VISION_MAX_BYTES, VISION_PER_HOUR, withinRate } from "./policy";
import { aiDm, drain, hosted, hostFetch, lobbyOf, PRIVATE_HEADERS, restarting, ROOM_TTL_MS, toBase64 } from "./shared";
import type { Env, RoomConfig } from "./shared";

interface Room { id: string; token: string; config: RoomConfig }
type Sock = { seat: string | null; opened: number; win: number; n: number };

export class HostedRoom extends DurableObject<Env> {
	private async room(): Promise<Room | null> {
		const config = await this.ctx.storage.get<RoomConfig>("config");
		if (!config) return null;
		let token = await this.ctx.storage.get<string>("token");
		if (!token) {
			token = [...crypto.getRandomValues(new Uint8Array(24))].map((b) => b.toString(16).padStart(2, "0")).join("");
			await this.ctx.storage.put("token", token);
		}
		return { id: (await this.ctx.storage.get<string>("id")) ?? "unknown", token, config };
	}

	/** One request to the room host: its own control calls (/__host/…, `roomId` unset), or a path on a room's table server.
	 *  A room's path always travels behind /__r/<id>, so no path a visitor sends can turn into a control call. */
	private host(path: string, init: { method?: string; headers?: Headers; body?: BodyInit | null }, roomId?: string, internal?: string): Promise<Response> {
		const headers = new Headers(init.headers);
		const from = headers.get("X-Forwarded-For") || "unknown";      // set by the Worker (the visitor's address) or by this object
		for (const h of PRIVATE_HEADERS) headers.delete(h);
		if (internal) headers.set("X-Room-Internal", internal);        // the room's own token: this call is the lobby's, not a visitor's
		if (roomId) {
			headers.set("X-Public-Host", new URL(this.env.PUBLIC_ORIGIN || "https://table.divinci.ai").host);
			// its own header: whatever sits between here and the room host may rewrite X-Forwarded-For, and the table tells
			// devices apart by it (a seat belongs to the device that claimed it)
			headers.set("X-Client-Addr", from.slice(0, 100));
		}
		const to = roomId ? `/__r/${roomId}${path.startsWith("/") ? path : "/" + path}` : path;
		return hostFetch(this.env, to, { method: init.method, headers, body: init.body });
	}

	/** A room lives as long as it is listed: six hours, or thirty minutes for an agent table that never filled. After
	 *  that it is gone for good (its id can't be used to wake a process), and the room host is told to stop it. */
	private async endsAt(r: Room): Promise<number> {
		const created = (await this.ctx.storage.get<number>("created")) ?? 0;
		const unfilled = !!r.config.agents && !(await this.ctx.storage.get<boolean>("started"));
		return created ? created + (unfilled ? AGENT_OPEN_TABLE_TTL_MS : ROOM_TTL_MS) + 60_000 : 0;
	}

	private async expired(r: Room): Promise<boolean> {
		if (Date.now() < await this.endsAt(r)) return false;
		await this.expire(r.id);
		return true;
	}

	private async expire(id: string): Promise<void> {
		await this.ctx.storage.deleteAll();
		for (const ws of this.ctx.getWebSockets()) { try { ws.close(1001, "the room has closed"); } catch { /* gone */ } }
		for (let i = 0; i < 2 && hosted(this.env); i++) {              // (asked twice: a room left running would stay reachable until its time is up)
			const ok = await this.host(`/__host/rooms/${id}/sleep`, { method: "POST", body: "{}" }).then(async (x) => { await x.body?.cancel(); return x.ok; }).catch(() => false);
			if (ok) break;
		}
	}

	/** Start (or wake) the room's process. The kill switch must hold for a room that wakes again: no AI key then. */
	private async ensureRunning(r: Room): Promise<boolean> {
		const { off } = await lobbyOf(this.env).aiState();
		const aiOk = aiKeyAllowed((r.config.ai?.length ?? 0) > 0 || r.config.game === "dnd", off);
		const magic = (r.config.game ?? "magic") === "magic";
		const env: Record<string, string> = {
			FUSION_MAX_CALLS: this.env.FUSION_MAX_CALLS || "300",
			...(!off && magic && !r.config.agents && this.env.OPENMIC_JUDGE === "1" ? { OPENMIC_ROOM: "1", ROOM_ORIGIN: this.env.PUBLIC_ORIGIN || "https://table.divinci.ai" } : {}),
			...(aiOk && r.config.game === "dnd" && aiDm(this.env) ? { DND_CLOUD_AI: "1", DND_DM_RELEASE_ID: this.env.DND_DM_RELEASE_ID! } : {}),
			...(r.config.agents ? { TABLE_AI_PASS_SECS: "60" } : {}),       // an agent that has left is passed for after a minute
		};
		const res = await this.host(`/__host/rooms/${r.id}/ensure`, { method: "POST", headers: new Headers({ "Content-Type": "application/json" }),
			body: JSON.stringify({ config: r.config, token: r.token, ai: aiOk, env, expires_at: await this.endsAt(r) }) }).catch((e) => e as Error);
		if (res instanceof Error || !res.ok) {
			console.error("room host: ensure failed", res instanceof Error ? res.message : res.status);
			if (!(res instanceof Error)) await res.body?.cancel();
			return false;
		}
		await res.body?.cancel();
		return true;
	}

	/** One of this object's OWN requests to the room's table server (never a visitor's: those go from the Worker, proxy.ts).
	 *  A sleeping (or restarted) room is woken once and asked again. */
	private async toRoom(r: Room, path: string, init: { method?: string; headers?: Headers; body?: ArrayBuffer | string | null }): Promise<Response> {
		const send = () => this.host(path, init, r.id, r.token).catch((e) => e as Error);
		let res = await send();
		if (res instanceof Error || res.headers.has("X-Room-Down")) {        // set only by the room host itself (asleep, or no answer): never by a table
			if (!(res instanceof Error)) await res.body?.cancel();
			if (!(await this.ensureRunning(r))) return restarting();
			res = await send();
			if (res instanceof Error) return restarting();
		}
		return res;
	}

	private async seatOf(r: Room, key: string, addr: string): Promise<{ seat: string; deck?: string[] } | null> {
		const check = await this.toRoom(r, "/api/seat/check", { method: "POST", headers: new Headers({ "X-Seat-Key": key.slice(0, 200), "X-Forwarded-For": addr }) });
		if (!check.ok) { await check.body?.cancel(); return null; }
		return check.json<{ seat: string; deck?: string[] }>();
	}

	// ── presence: heads and hands of headset players, relayed between seated sockets (see TableRoom) ──
	async webSocketMessage(ws: WebSocket, msg: string | ArrayBuffer): Promise<void> {
		if (typeof msg !== "string" || msg.length > PRESENCE_MAX_BYTES) { ws.close(1009, "too large"); return; }
		const a = ws.deserializeAttachment() as Sock;
		let d: { t?: string; key?: unknown; mode?: unknown } & Record<string, unknown>;
		try { d = JSON.parse(msg); } catch { return; }
		if (!a.seat) {
			if (d?.t !== "auth" || typeof d.key !== "string" || Date.now() - a.opened > 15_000) { ws.close(1008, "auth first"); return; }
			const r = await this.room();
			if (!r) { ws.close(1011, "no room"); return; }
			const who = await this.seatOf(r, d.key, "presence");
			if (!who) { ws.send(JSON.stringify({ t: "error", error: "claim your seat first (👤)" })); ws.close(1008, "not seated"); return; }
			for (const o of this.ctx.getWebSockets()) {
				if (o !== ws && (o.deserializeAttachment() as Sock | null)?.seat === who.seat) o.close(1000, "replaced by a newer connection");
			}
			ws.serializeAttachment({ ...a, seat: who.seat });
			ws.send(JSON.stringify({ t: "hello", seat: who.seat }));
			return;
		}
		const now = Date.now();
		if (now - a.win > 1000) { a.win = now; a.n = 0; }
		if (++a.n > PRESENCE_MAX_PER_SEC) return;
		ws.serializeAttachment(a);
		if (d?.t !== "pose") return;
		const p = parsePose(d);
		if (!p) return;
		const out = JSON.stringify({ t: "pose", seat: a.seat, ...p, mode: d.mode === "ar" ? "ar" : "vr" });
		for (const o of this.ctx.getWebSockets()) {
			if (o !== ws && (o.deserializeAttachment() as Sock | null)?.seat) { try { o.send(out); } catch { /* gone */ } }
		}
	}

	async webSocketClose(ws: WebSocket): Promise<void> {
		const seat = (ws.deserializeAttachment() as Sock | null)?.seat;
		if (!seat) return;
		const out = JSON.stringify({ t: "leave", seat });
		for (const o of this.ctx.getWebSockets()) {
			if (o !== ws && (o.deserializeAttachment() as Sock | null)?.seat !== seat) { try { o.send(out); } catch { /* gone */ } }
		}
	}

	/** The open mic's judge (Clef-Flash): only the room's own process may ask (it alone holds the room token). */
	private async judge(request: Request, r: Room): Promise<Response> {
		if (request.method !== "POST" || !(await sameText(request.headers.get("X-Room-Token") ?? "", r.token))) return new Response("Not found", { status: 404 });
		if (!this.env.AI || (await lobbyOf(this.env).aiState()).off) return Response.json({ error: "paused" }, { status: 503 });
		const raw = await request.text();
		if (raw.length > 20_000) return Response.json({ error: "too large" }, { status: 413 });
		const now = Date.now();
		const { kept, ok } = withinRate((await this.ctx.storage.get<number[]>("xr:judge")) ?? [], now, 600_000, 200);
		if (!ok) return Response.json({ error: "too many lines" }, { status: 429 });
		await this.ctx.storage.put("xr:judge", [...kept, now]);
		let body: { state?: unknown; questions?: unknown };
		try { body = JSON.parse(raw); } catch { return Response.json({ error: "not JSON" }, { status: 400 }); }
		try {
			const out = await this.env.AI.run("@cf/cloudflare/clef-flash", { state: body.state, questions: body.questions }) as { answers?: unknown };
			return out?.answers ? Response.json({ answers: out.answers }) : Response.json({ error: "no answers" }, { status: 502 });
		} catch (e) {
			console.error("clef judge failed", (e as Error).message);
			return Response.json({ error: "judge unavailable" }, { status: 502 });
		}
	}

	/** The headset's voice and board photos, for seated players only (see TableRoom.xrAi for the rules). */
	private async xrAi(request: Request, r: Room, kind: "stt" | "vision"): Promise<Response> {
		if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
		const who = await this.seatOf(r, request.headers.get("X-Seat-Key") ?? "", request.headers.get("X-Forwarded-For") ?? "unknown");
		if (!who) return Response.json({ error: "claim your seat first (👤)", need_seat: true }, { status: 403 });
		const { seat, deck } = who;
		if (kind === "vision" && new URL(request.url).searchParams.get("passive") === "1" && !deck?.length) {
			return Response.json({ error: "watching your board needs your deck — pick it in the lobby (Your deck / physical deck)" }, { status: 412 });
		}
		if (!this.env.AI || (await lobbyOf(this.env).aiState()).off) return Response.json({ error: "voice and photo reading are paused right now" }, { status: 503 });
		const body = await request.arrayBuffer();
		if (!body.byteLength || body.byteLength > (kind === "stt" ? STT_MAX_BYTES : VISION_MAX_BYTES)) {
			return Response.json({ error: kind === "stt" ? "keep it under a minute" : "that photo is too large" }, { status: 413 });
		}
		const rk = "xr:" + kind, now = Date.now();
		const { kept, ok } = withinRate((await this.ctx.storage.get<number[]>(rk)) ?? [], now, kind === "stt" ? 600_000 : 3_600_000, kind === "stt" ? STT_PER_10_MIN : VISION_PER_HOUR);
		if (!ok) return Response.json({ error: kind === "stt" ? "too much talking for a moment — try again shortly" : "too many photos this hour" }, { status: 429 });
		await this.ctx.storage.put(rk, [...kept, now]);
		const b64 = toBase64(body);
		try {
			if (kind === "stt") {
				const out = await this.env.AI.run("@cf/openai/whisper-large-v3-turbo", { audio: b64, initial_prompt: speechHint(deck) }) as { text?: string };
				return Response.json({ seat, text: String(out?.text ?? "").replace(/\s+/g, " ").trim().slice(0, 400) });
			}
			const out = await this.env.AI.run("@cf/meta/llama-4-scout-17b-16e-instruct", { max_tokens: 900, messages: [
				{ role: "system", content: boardPrompt(deck) },
				{ role: "user", content: [{ type: "text", text: "List the cards on this player's side of the table." },
					{ type: "image_url", image_url: { url: "data:" + (request.headers.get("Content-Type") || "image/jpeg") + ";base64," + b64 } }] },
			] }) as { response?: unknown };
			const text = typeof out?.response === "string" ? out.response : JSON.stringify(out?.response ?? "");
			return Response.json({ seat, cards: parseBoardReply(text), deck_known: !!deck?.length });
		} catch (e) {
			console.error("xr ai failed", kind, (e as Error).message);
			return Response.json({ error: kind === "stt" ? "couldn't hear that — try again" : "couldn't read that photo — try again" }, { status: 502 });
		}
	}

	/** An agent takes a seat (the lobby has already checked the agent and its limits). The room sees one "device" per
	 *  agent, so the same agent can claim its seat again (a lost key) and nobody else can. When the last seat is taken
	 *  the game starts. */
	private async agentClaim(r: Room, seat: string, agentId: string): Promise<Response> {
		const seats = (r.config.pilots ?? []).map((p) => p.name);
		if (!r.config.agents || !seats.includes(seat)) return Response.json({ error: "no such seat" }, { status: 404 });
		const res = await this.toRoom(r, "/api/seat/claim", { method: "POST", body: JSON.stringify({ name: seat }),
			headers: new Headers({ "Content-Type": "application/json", "X-Forwarded-For": "agent:" + agentId, "User-Agent": "divinci-table-agent" }) });
		if (!res.ok) return res;
		const { key } = await res.json<{ key: string }>();
		const taken = new Set((await this.ctx.storage.get<string[]>("agentSeats")) ?? []);
		taken.add(seat);
		await this.ctx.storage.put("agentSeats", [...taken]);
		let started = !!(await this.ctx.storage.get<boolean>("started"));
		if (!started && seats.every((s) => taken.has(s))) {
			const go = await this.toRoom(r, "/api/phase/next", { method: "POST", body: JSON.stringify({ by: seat, key }),
				headers: new Headers({ "Content-Type": "application/json", "X-Seat-Key": key, "X-Forwarded-For": "agent:" + agentId }) });
			started = go.ok;
			await go.body?.cancel();
			if (started) {
				await this.ctx.storage.put("started", true);
				await this.ensureRunning(r);                         // the room now lasts as long as a game, not as long as an open table
			}
		}
		return Response.json({ seat, key, started, waiting_for: seats.filter((s) => !taken.has(s)) });
	}

	async fetch(request: Request): Promise<Response> {
		const url = new URL(request.url);
		if (url.pathname === "/__room/setup" && request.method === "POST") {
			await this.ctx.storage.put({ config: await request.json(), id: url.searchParams.get("id") ?? "unknown", created: Date.now() });
			return new Response("ok");
		}
		const r = await this.room();
		if (!r || url.pathname === "/__room/expire" || await this.expired(r)) {
			if (r && url.pathname === "/__room/expire") await this.expire(r.id);       // admin: the room is closed now
			await drain(request);
			return new Response("This room doesn't exist (or has expired).", { status: 404 });
		}
		if (!hosted(this.env)) return Response.json({ error: "This lobby has no room host configured." }, { status: 503 });
		if (url.pathname === "/__room/ensure" && request.method === "POST") {      // the Worker found the room asleep
			return (await this.ensureRunning(r)) ? new Response("ok") : restarting();
		}
		if (url.pathname === "/__room/agent-claim" && request.method === "POST") {
			return this.agentClaim(r, url.searchParams.get("seat") ?? "", url.searchParams.get("agent") ?? "");
		}
		if (url.pathname === "/__room/release") {                  // admin: free a seat whose device is lost
			if (!(await this.ensureRunning(r))) return restarting();
			const seat = url.searchParams.get("seat") ?? "";
			const res = await this.host(`/__host/rooms/${r.id}/release`, { method: "POST", headers: new Headers({ "Content-Type": "application/json" }), body: JSON.stringify({ seat }) });
			if (res.ok) {
				await lobbyOf(this.env).markReleased(r.id, seat);
				await this.ctx.storage.put("agentSeats", ((await this.ctx.storage.get<string[]>("agentSeats")) ?? []).filter((s) => s.toLowerCase() !== seat.toLowerCase()));
			}
			return res;
		}
		if (url.pathname === "/__room/sleep") {                    // admin: save now, then stop the process
			const res = await this.host(`/__host/rooms/${r.id}/sleep`, { method: "POST", body: "{}" });
			await res.body?.cancel();
			return Response.json({ slept: res.ok });
		}
		if (/^\/__(room|host|r)(\/|$)/.test(url.pathname)) { await drain(request); return new Response("Not found", { status: 404 }); }
		if (url.pathname === "/api/xr/judge") return this.judge(request, r);
		if (url.pathname === "/api/xr/presence") {
			if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") return new Response("WebSocket only", { status: 426 });
			const [client, server] = Object.values(new WebSocketPair());
			this.ctx.acceptWebSocket(server);
			server.serializeAttachment({ seat: null, opened: Date.now(), win: 0, n: 0 } satisfies Sock);
			return new Response(null, { status: 101, webSocket: client });
		}
		if (url.pathname === "/api/xr/stt" || url.pathname === "/api/xr/vision") return this.xrAi(request, r, url.pathname.endsWith("stt") ? "stt" : "vision");
		await drain(request);                                      // a room's ordinary traffic does not come through here (proxy.ts)
		return new Response("Not found", { status: 404 });
	}
}
