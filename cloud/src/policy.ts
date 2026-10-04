// The lobby's rules for AI seats and admin calls, kept free of Cloudflare APIs so Node can test them
// (cloud/test/policy.test.ts: `node --experimental-strip-types --test cloud/test/policy.test.ts`).

/** Constant-time comparison of two strings (through SHA-256, so lengths don't leak either). */
export async function sameText(a: string, b: string): Promise<boolean> {
	const enc = new TextEncoder();
	const [x, y] = await Promise.all([a, b].map((t) => crypto.subtle.digest("SHA-256", enc.encode(t))));
	const u = new Uint8Array(x), v = new Uint8Array(y);
	let diff = 0;
	for (let i = 0; i < u.length; i++) diff |= u[i] ^ v[i];
	return diff === 0;
}

/** An admin call carries `Authorization: Bearer <ADMIN_TOKEN>`; no token configured means no admin at all. */
export async function isAdmin(authorization: string | null, token: string | undefined): Promise<boolean> {
	if (!token) return false;
	return sameText(authorization ?? "", `Bearer ${token}`);
}

export const CODE_FAILS_PER_HOUR = 8;          // wrong invitation codes from one address
export const CODE_FAILS_GLOBAL_PER_HOUR = 60;  // and from everyone: past this, codes aren't even checked for an hour

export interface AiRoomAsk {
	configured: boolean;     // the key, the release map and an invitation code all exist
	codeOk: boolean;         // the code given matches
	failsHere: number;       // wrong codes from this address in the last hour (before this one)
	failsEverywhere: number; // wrong codes from anyone in the last hour
	off: boolean;            // the kill switch
	today: number;           // AI rooms already started today
	perDay: number;
}

/** Why an AI room can't start, or null when it can. Order matters: a locked-out guesser learns nothing. */
export function aiRoomRefusal(a: AiRoomAsk): string | null {
	if (!a.configured) return "AI players aren't available on this server yet.";
	if (a.failsHere >= CODE_FAILS_PER_HOUR || a.failsEverywhere >= CODE_FAILS_GLOBAL_PER_HOUR) {
		return "Too many wrong invitation codes. Try again in an hour.";
	}
	if (!a.codeOk) return "AI players are by invitation for now: enter your invitation code, or leave the AI seats unticked.";
	if (a.off) return "AI players are paused right now. Start a table for people, or try again later.";
	if (a.today >= a.perDay) return "Today's AI tables are all taken. Start a table for people, or try again tomorrow.";
	return null;
}

/** May a (re)starting room's container get the AI key? Not while the kill switch is on. */
export function aiKeyAllowed(hasAiSeats: boolean, off: boolean): boolean {
	return hasAiSeats && !off;
}

// ── voice and photos from the headset (the room's Worker runs Workers AI for them) ───────────────────────
export const STT_PER_10_MIN = 120;         // per room (an open mic sends a segment per spoken line)
export const VISION_PER_HOUR = 90;         // per room (passive watching sends at most one every 30 s per player)
export const STT_MAX_BYTES = 3_000_000;    // ~1 min of opus/webm
export const VISION_MAX_BYTES = 5_000_000;

/** Times inside the window, and whether one more is allowed. */
export function withinRate(times: number[], now: number, windowMs: number, max: number): { kept: number[]; ok: boolean } {
	const kept = times.filter((t) => now - t < windowMs);
	return { kept, ok: kept.length < max };
}

export interface SeenCard { name: string; tapped: boolean; count: number }

/** The vision model's answer → a clean card list: JSON anywhere in the reply, names trimmed to plain text,
 *  counts 1–20, at most 60 entries. Anything else is dropped, never passed on. */
export function parseBoardReply(text: string): SeenCard[] {
	const m = String(text ?? "").match(/\[[\s\S]*\]|\{[\s\S]*\}/);
	if (!m) return [];
	let raw: unknown;
	try { raw = JSON.parse(m[0]); } catch { return []; }
	const list = Array.isArray(raw) ? raw : Array.isArray((raw as { cards?: unknown }).cards) ? (raw as { cards: unknown[] }).cards : [];
	const out: SeenCard[] = [];
	for (const c of list.slice(0, 60)) {
		if (!c || typeof c !== "object") continue;
		const name = String((c as { name?: unknown }).name ?? "").replace(/[^\p{L}\p{N} ,'’\-:/!?.]/gu, "").replace(/\s+/g, " ").trim().slice(0, 80);
		if (!name) continue;
		const n = Math.round(Number((c as { count?: unknown }).count ?? 1));
		out.push({ name, tapped: (c as { tapped?: unknown }).tapped === true, count: Number.isFinite(n) ? Math.min(20, Math.max(1, n)) : 1 });
	}
	return out;
}

/** The photo prompt; with the seat's deck list the model reads names against real candidates (measured on a game-3
 *  photo: 6/9 cards found and 0 wrong with the list, 5/9 and 0 wrong without; sideways (tapped) cards were missed
 *  either way, so a reading only ever ADDS to a board). Names are sanitised and capped before they reach the prompt. */
export function boardPrompt(deck: unknown): string {
	const names = (Array.isArray(deck) ? deck : []).map((n) => String(n).replace(/[^\p{L}\p{N} ,'’\-:/!?.]/gu, "").trim().slice(0, 80))
		.filter(Boolean).slice(0, 200);
	return names.length ? BOARD_PROMPT + " The cards on this table all come from this player's deck; each name you give MUST be one of " +
		"these exactly: " + names.join("; ") + "." : BOARD_PROMPT;
}

/** Whisper's vocabulary hint: the game's words and (when known) the seat's own card names, so "Llanowar" isn't heard
 *  as "lanour". Cleaned and capped (Whisper reads only a few hundred tokens of it). */
export function speechHint(deck: unknown): string {
	const names = (Array.isArray(deck) ? deck : []).map((n) => String(n).replace(/[^\p{L}\p{N} ,'’\-]/gu, "").trim()).filter(Boolean);
	let s = "A game of Magic: The Gathering, Commander. I cast, I attack, tap, untap, mana, graveyard, exile, my turn, pass, NEXT.";
	for (const n of names) { if ((s + " " + n + ",").length > 800) break; s += " " + n + ","; }
	return s;
}

export const BOARD_PROMPT = "You see a photo of one player's side of a Magic: The Gathering table. List the cards that are " +
	"face up ON THE TABLE in front of this player (their battlefield). Ignore cards held in hands, card backs, sleeves, " +
	"dice and anything you cannot read. A card turned sideways is tapped. Group identical cards. Reply with JSON only: " +
	'[{"name": "<exact English card name>", "tapped": false, "count": 1}]. If unsure of a name, leave that card out.';

// ── presence: head and hands of headset players, in TABLE coordinates (metres at life size) ─────────────
export const PRESENCE_MAX_BYTES = 600;
export const PRESENCE_MAX_PER_SEC = 30;
export interface Pose { h: number[]; l: number[] | null; r: number[] | null }

/** A pose message from a headset, or null. Each part is [x, y, z, qx, qy, qz, qw]: finite, positions within 20 m of
 *  the table, quaternions normalised. Anything else is dropped, so a client can't send junk to other headsets. */
export function parsePose(raw: unknown): Pose | null {
	if (!raw || typeof raw !== "object") return null;
	const part = (v: unknown): number[] | null => {
		if (!Array.isArray(v) || v.length !== 7 || !v.every((n) => typeof n === "number" && Number.isFinite(n))) return null;
		if (v.slice(0, 3).some((n) => Math.abs(n) > 20)) return null;
		const len = Math.hypot(v[3], v[4], v[5], v[6]);
		if (len < 0.5 || len > 1.5) return null;
		return [...v.slice(0, 3).map((n) => Math.round(n * 1000) / 1000), ...v.slice(3).map((n) => Math.round((n / len) * 10000) / 10000)];
	};
	const p = raw as { h?: unknown; l?: unknown; r?: unknown };
	const h = part(p.h);
	if (!h) return null;
	return { h, l: p.l == null ? null : part(p.l), r: p.r == null ? null : part(p.r) };
}
