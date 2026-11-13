// Types and small helpers shared by the lobby Worker (index.ts), the hosted room (hosted.ts) and the agent API (agents.ts).
import type { HostedRoom } from "./hosted";
import type { Lobby, TableRoom } from "./index";

export interface Env {
	ROOM: DurableObjectNamespace<TableRoom | HostedRoom>;
	LOBBY: DurableObjectNamespace<Lobby>;
	DIVINCI_FUSION_API_KEY?: string;
	ADMIN_TOKEN?: string;
	ROOM_SNAPSHOTS?: R2Bucket;        // container rooms only (a hosted room is saved by its room host)
	ASSETS_BUCKET?: R2Bucket;         // avatars and D&D rooms; without it they come from the room host's --assets folder
	GIT_SHA?: string;               // the commit this Worker was deployed from (scripts/cloud_deploy.sh --var)
	FUSION_CONFIG?: string;
	AI?: { run(model: string, input: unknown): Promise<unknown> };
	PUBLIC_ORIGIN?: string;          // this Worker's public address, for the room container's callbacks (the Clef judge)   // Workers AI: speech-to-text and photo reading for /xr
	AI_INVITE_CODE?: string;        // AI seats in public rooms need this code (unset = no AI seats at all)
	FUSION_MAX_CALLS?: string;      // per-room cap on an AI seat's release requests (default 300)
	AI_ROOMS_PER_DAY?: string;      // new AI rooms per UTC day across the whole lobby (default 4)
	DND_CLOUD_AI?: string;          // "1" = AI Dungeon Master in public rooms (a budget decision; unset = a person DMs)
	DND_DM_RELEASE_ID?: string;
	ROOM_HOST?: Fetcher;            // rooms run on a room host (cloud/roomhost.py), reached privately through a tunnel (a Workers VPC service) …
	ROOM_HOST_ORIGIN?: string;      // … or at this https address
	ROOM_HOST_KEY?: string;         // either way it answers only to this key (a secret)
	MAX_LIVE_ROOMS?: string;        // people's tables listed at once (default 8)
	MAX_AGENT_TABLES?: string;      // agent tables listed at once (default 6)
	HOST_AI_SEATS?: string;         // "1" = the room host holds DIVINCI_FUSION_API_KEY and FUSION_CONFIG, so AI seats can be offered
	OPENMIC_JUDGE?: string;         // "1" = hosted Magic rooms use the Clef-judged open mic (needs Workers AI beyond the free daily amount)
}

export interface AiSeat { name: string; commander: string; deck: string }
export interface RoomConfig { humans: string[]; ai: AiSeat[]; pilots?: AiSeat[]; game: "magic" | "chess" | "dnd"; white?: string; black?: string; minutes?: number; increment?: number; dm?: string;
	mode?: "map" | "theater";       // D&D: the battle map, or theater of the mind (docs/THEATER-GOAL.md)
	agents?: boolean }               // an agent table: every seat is a virtual deck driven over the seat API (docs/agents.md)
export interface RoomInfo { id: string; title: string; game: string; humans: string[]; ai: string[]; created: number; claimed?: string[];
	agents?: boolean; decks?: Record<string, string>; by?: Record<string, { id: string; name: string }> }   // agent tables: seat → commander, seat → the agent in it

// AI opponents a public room may seat. Each is played by a Divinci release (one per deck).
export const AI_DECKS: Record<string, { commander: string; deck: string }> = {
	tuvasa: { commander: "Tuvasa the Sunlit", deck: "decks/tuvasa.json" },
	kaust: { commander: "Kaust, Eyes of the Glade", deck: "decks/kaust.json" },
	ellivere: { commander: "Ellivere of the Wild Court", deck: "decks/ellivere.json" },
};
// Virtual decks a PERSON can play (testing without cards): the seat becomes a pilot seat driven from /hand.
export const PILOT_DECKS: Record<string, { commander: string; deck: string }> = {
	inspirit: { commander: "Inspirit, Flagship Vessel", deck: "decks/inspirit.json" },
	aminatou: { commander: "Aminatou, the Fateshifter", deck: "decks/aminatou.json" },
	nghathrod: { commander: "Captain N'ghathrod", deck: "decks/nghathrod.json" },
	elsha: { commander: "Elsha of the Infinite", deck: "decks/elsha.json" },
	...AI_DECKS,
};
export const ROOM_TTL_MS = 6 * 3600_000;      // a room is listed for six hours
export const MAX_LIVE_ROOMS = 8;       // the default; containers.max_instances caps container rooms
export const MAX_CREATES_PER_HOUR = 3;        // per address
export const NAME_RE = /^[\p{L}\p{N}][\p{L}\p{N} '._-]{0,23}$/u;     // starts with a letter or a digit (a name is also a command-line value)


export function toBase64(buf: ArrayBuffer): string {
	const bytes = new Uint8Array(buf);
	let s = "";
	for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
	return btoa(s);
}

/** Can this lobby seat release-played AI at all? With containers the Worker hands the key to the room. With a room host the
 *  key lives on the host (docs/room-host.md), and HOST_AI_SEATS=1 is how the Worker is told it is there. */
export const aiSeatsHere = (env: Env) => !!env.DIVINCI_FUSION_API_KEY && (!(env.ROOM_HOST || env.ROOM_HOST_ORIGIN) || env.HOST_AI_SEATS === "1");

/** Is a room host configured? (the private binding or an address, and the key) */
export const hosted = (env: Env) => !!((env.ROOM_HOST || env.ROOM_HOST_ORIGIN) && env.ROOM_HOST_KEY);

/** One request to the room host, with the key. Through the private binding when there is one (the name in the URL is
 *  not looked up: the binding decides where it goes), otherwise to ROOM_HOST_ORIGIN. `path` starts with "/". */
export function hostFetch(env: Env, path: string, init: { method?: string; headers?: Headers; body?: BodyInit | null } = {}): Promise<Response> {
	const headers = new Headers(init.headers);
	headers.set("X-Room-Host-Key", env.ROOM_HOST_KEY ?? "");
	const req = { method: init.method ?? "GET", headers, body: init.body ?? null, redirect: "manual" as const };
	return env.ROOM_HOST ? env.ROOM_HOST.fetch("http://room-host" + path, req) : fetch(env.ROOM_HOST_ORIGIN!.replace(/\/+$/, "") + path, req);
}

export const aiDm = (env: Env) => env.DND_CLOUD_AI === "1" && !!env.DND_DM_RELEASE_ID && aiSeatsHere(env);

export const newRoomId = () => [...crypto.getRandomValues(new Uint8Array(8))].map((b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");

export const lobbyOf = (env: Env) => env.LOBBY.get(env.LOBBY.idFromName("lobby"));

/** Read and drop a request's body (up to `max` bytes, then give up on it). A refusal that leaves the body unread can
 *  break the connection it arrived on (seen with `wrangler dev`: the NEXT request died "Network connection lost"). */
export async function drain(request: Request, max = 1_000_000): Promise<void> {
	await readCapped(request, 0, max);
}

/** Up to `keep` bytes of the body as text (null when it is longer); the rest is read and dropped as in drain(). */
export async function readCapped(request: Request, keep: number, max = 1_000_000): Promise<string | null> {
	const reader = request.body?.getReader();
	if (!reader) return "";
	const parts: Uint8Array[] = [];
	let n = 0;
	try {
		for (;;) {
			const { done, value } = await reader.read();
			if (done) break;
			n += value.byteLength;
			if (n <= keep) parts.push(value);
			if (n > max) { await reader.cancel(); break; }
		}
	} catch { return null; }
	if (n > keep) return null;
	const all = new Uint8Array(n);
	let at = 0;
	for (const p of parts) { all.set(p, at); at += p.byteLength; }
	return new TextDecoder().decode(all);
}
