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
export const STT_PER_10_MIN = 40;          // per room
export const VISION_PER_HOUR = 20;         // per room
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

export const BOARD_PROMPT = "You see a photo of one player's side of a Magic: The Gathering table. List the cards that are " +
	"face up ON THE TABLE in front of this player (their battlefield). Ignore cards held in hands, card backs, sleeves, " +
	"dice and anything you cannot read. A card turned sideways is tapped. Group identical cards. Reply with JSON only: " +
	'[{"name": "<exact English card name>", "tapped": false, "count": 1}]. If unsure of a name, leave that card out.';
