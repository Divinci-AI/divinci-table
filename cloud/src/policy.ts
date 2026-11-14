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

/** A D&D room file the Worker may serve from R2 (dnd/<rel>), or null. Same shape the laptop server allows
 *  (table/dnd_server.py): a location's room/map/view or a mini, nothing else. Only approved locations are ever
 *  uploaded (scripts/dnd_upload.py), so R2 holds no drafts for this to leak. */
const DND_TYPES: Record<string, string> = { glb: "model/gltf-binary", jpg: "image/jpeg", png: "image/png", mp4: "video/mp4" };
export function dndAssetKey(pathname: string): { key: string; type: string } | null {
	if (!pathname.startsWith("/dnd-assets/")) return null;
	let rel: string;
	try { rel = decodeURIComponent(pathname.slice("/dnd-assets/".length)); } catch { return null; }
	const m = /^(locations\/[a-z0-9-]{1,40}\/[a-z_]{1,20}|minis\/[a-z0-9-]{1,40})\.(glb|jpg|png|mp4)$/.exec(rel);
	return m ? { key: "dnd/" + rel, type: DND_TYPES[m[2]] } : null;
}

// ── agents: outside AI players that register, find a table and take a seat with their own key (docs/agents.md) ─────
// 3–32 plain characters. ASCII only: a name is shown to people as who they are playing, and a letter from another
// alphabet that looks like a Latin one is how one name passes for another.
export const AGENT_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 ._-]{1,30}[A-Za-z0-9.]$/;
// Names only the lobby's admin may register (its own players, and names an agent could pass itself off with).
const HOUSE_WORDS = /^(fusion|claude|leonardo|divinci|admin|host|house|table|lobby|system|anthropic|official)/;
/** Is this one of the lobby's own names? Judged on the letters and digits alone, so "House Autopilot", "HouseAutopilot" and
 *  "h.o.u.s.e" are the same name. */
export const isHouseName = (name: string) => HOUSE_WORDS.test(name.toLowerCase().replace(/[^a-z0-9]/g, ""));

// AI vendors and their models. Nobody but the lobby's admin may present an agent, a seat or a table as one of these, so a
// visitor watching "ChatGPT vs Gemini" is never looking at somebody else's bot wearing a vendor's name.
// Reserved ANYWHERE in the name ("SuperClaude", "my gpt bot"), after spaces, dots, case, accents, letters from other
// alphabets that look Latin, and digits standing in for letters are taken away ("C l 4 u d e", "Сlaude" with a Cyrillic С).
const VENDOR_WORDS = ["chatgpt", "openai", "gpt", "gemini", "google", "claude", "anthropic", "llama", "grok", "mistral", "deepseek",
	"copilot", "microsoft"];
// Too short or too common inside ordinary words ("metal", "Xaio"): reserved only as a word of their own ("Meta AI", "MetaBot", "x.ai").
const VENDOR_TOKENS = ["meta", "xai"];
const LOOKALIKES: Record<string, string> = {
	// Cyrillic
	"а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
	"і": "i", "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "ӏ": "l", "ԛ": "q", "ԝ": "w",
	// Greek
	"α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "γ": "y", "η": "n",
	// Latin look-alikes NFKD leaves alone
	"ı": "i", "ł": "l", "ø": "o", "đ": "d", "ß": "ss", "ɩ": "i", "ʟ": "l",
};
const DIGITS: Record<string, string> = { "0": "o", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "|": "l", "€": "e" };

/** The name with accents dropped, invisible characters removed and look-alike letters made Latin; case kept. */
function plainLetters(name: string): string {
	return [...String(name ?? "").normalize("NFKD").replace(/[\p{M}\p{Cf}]/gu, "")]
		.map((c) => LOOKALIKES[c] ?? LOOKALIKES[c.toLowerCase()]?.toUpperCase() ?? c).join("");
}

/** "1" is read both ways ("C1aude", "Gem1n1"); every other stand-in has one reading. */
const unDigit = (s: string) => {
	const base = [...s].map((c) => DIGITS[c] ?? c).join("");
	return [base.replace(/1/g, "l"), base.replace(/1/g, "i")];
};

/** Is this an AI vendor's or model's name (see VENDOR_WORDS)? For agent names, seat names and table titles alike. */
export function isVendorName(name: string): boolean {
	const plain = plainLetters(name);
	const squashed = unDigit(plain.toLowerCase()).map((s) => s.replace(/[^a-z]/g, ""));
	if (squashed.some((s) => VENDOR_WORDS.some((w) => s.includes(w)) || s.includes("metaai"))) return true;
	// words: split at anything not a letter or digit, and also where a capital follows a small letter ("MetaBot" → Meta, Bot);
	// a word and the next one together count too ("x.ai", "Meta AI")
	const split = (t: string) => t.toLowerCase().split(/[^a-z0-9@$!|€]+/).filter(Boolean);
	for (const words of [split(plain), split(plain.replace(/([a-z])([A-Z])/g, "$1 $2"))]) {
		const pairs = words.slice(1).map((w, i) => words[i] + w);
		if ([...words, ...pairs].some((w) => unDigit(w).some((v) => VENDOR_TOKENS.includes(v)))) return true;
	}
	return false;
}

export const RESERVED_NAME_ERROR = "That name is reserved: the names of AI companies and models (ChatGPT, Claude, Gemini, Llama…) " +
	"can't be used for seats, agents or tables here.";

/** DAILY_REQUEST_BUDGET as a number: anything that is not a positive number means the default, never "closed" or "no limit". */
export const dailyBudget = (v: string | undefined) => { const n = Number(v); return Number.isFinite(n) && n >= 1000 ? Math.floor(n) : 100_000; };
export const AGENT_REG_PER_HOUR = 3;            // new agents from one address
export const AGENT_REG_PER_DAY = 10;
export const AGENT_REG_GLOBAL_PER_DAY = 200;    // and from everyone: past this, sign-up is closed until tomorrow (UTC)
export const AGENT_TABLES_PER_HOUR = 2;         // tables one agent may start
export const AGENT_CLAIMS_PER_HOUR = 6;         // seats one agent may take (a re-claim of its own seat counts)
export const AGENT_SEATS_AT_ONCE = 2;           // live tables one agent may sit at
export const AGENT_OPEN_TABLES_PER_ADDRESS = 2; // tables waiting for players that were started from one address
export const AGENT_BAD_KEYS_PER_10_MIN = 20;    // wrong agent keys from one address before it is told to wait
export const AGENT_OPEN_TABLE_TTL_MS = 30 * 60_000;   // an agent table nobody filled is delisted after this
export const AGENT_BODY_MAX = 4_000;            // bytes of JSON an agent endpoint reads

export interface AgentRegAsk { hereHour: number; hereDay: number; everywhereDay: number; nameTaken: boolean; nameOk: boolean; nameReserved?: boolean }

/** Why a sign-up is refused ({status, error, retryAfter seconds}), or null. The limits come before the name, so a
 *  caller over its limit learns nothing about which names exist. */
export function agentRegRefusal(a: AgentRegAsk): { status: number; error: string; retryAfter?: number } | null {
	if (a.everywhereDay >= AGENT_REG_GLOBAL_PER_DAY) return { status: 429, error: "Sign-up is closed for today. Try again tomorrow (UTC).", retryAfter: 3600 };
	if (a.hereHour >= AGENT_REG_PER_HOUR) return { status: 429, error: "Too many new agents from this address this hour.", retryAfter: 3600 };
	if (a.hereDay >= AGENT_REG_PER_DAY) return { status: 429, error: "Too many new agents from this address today.", retryAfter: 6 * 3600 };
	if (!a.nameOk) return { status: 400, error: "name: 3 to 32 plain letters (a-z), numbers, spaces, dots, dashes or underscores." };
	if (a.nameReserved) return { status: 400, error: RESERVED_NAME_ERROR };
	if (a.nameTaken) return { status: 409, error: "That name is taken." };
	return null;
}

/** A new agent key: shown once, kept only as its SHA-256. */
export function newAgentKey(): string {
	const b = crypto.getRandomValues(new Uint8Array(32));
	return "dta_" + btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function sha256Hex(text: string): Promise<string> {
	const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
	return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The agent key from `Authorization: Bearer dta_…`, or null when the header is anything else. */
export function bearerAgentKey(authorization: string | null): string | null {
	const m = /^Bearer (dta_[A-Za-z0-9_-]{43})$/.exec(authorization ?? "");
	return m ? m[1] : null;
}

export interface AgentTableAsk { seats: unknown; decks: unknown; title: unknown }
export interface AgentTablePlan { seats: number; decks: string[]; title: string }

/** A request to start an agent table → 2–4 seats, one known deck each (asked for, or filled from `shuffled`, never the
 *  same deck twice), a plain title. Returns an error string when the request can't be met. */
export function planAgentTable(ask: AgentTableAsk, known: string[], shuffled: string[], agentName: string): AgentTablePlan | string {
	const want = Array.isArray(ask.decks) ? ask.decks.map((d) => String(d).toLowerCase().trim()) : [];
	const seats = ask.seats === undefined || ask.seats === null ? Math.max(2, want.length) : Number(ask.seats);
	if (!Number.isInteger(seats) || seats < 2 || seats > 4) return "seats: 2, 3 or 4.";
	if (want.length > seats) return "decks: at most one per seat.";
	if (want.some((d) => !known.includes(d))) return "decks: choose from " + known.join(", ") + ".";
	if (new Set(want).size !== want.length) return "decks: each deck once per table.";
	const decks = [...want, ...shuffled.filter((d) => known.includes(d) && !want.includes(d))].slice(0, seats);
	if (decks.length < seats) return "Not enough decks for that many seats.";
	const asked = String(ask.title ?? "").replace(/[^\p{L}\p{N} '._!?-]/gu, "").replace(/\s+/g, " ").trim().slice(0, 40);
	if (asked && isVendorName(asked)) return "title: " + RESERVED_NAME_ERROR;   // (the default is built from the agent's own, already checked, name)
	return { seats, decks, title: asked || `${agentName}'s table` };
}

export const AGENT_OVER_LINGER_MS = 10 * 60_000;      // a finished agent game stays listed this long (to look at the last board)
export const AGENT_RESULTS_KEPT = 50;                 // finished agent games in the public record
export const REQUEST_SAMPLE = 50;                     // the daily budget counts one request in this many (and adds this many)

/** The agent API has used its requests for the day (DAILY_REQUEST_BUDGET): agent tables rest until midnight UTC. */
export function budgetRefusal(now = Date.now()): Response {
	const left = Math.max(60, 86_400 - Math.floor((now % 86_400_000) / 1000));
	return Response.json({ error: "The agent tables are resting until midnight UTC: today's request budget is used up." }, { status: 503, headers: { "Retry-After": String(left) } });
}

/** What the room host says about a finished agent game, made safe to keep: an event we know, a winner that is one of the
 *  table's seats, whole numbers in a sane range. Anything else becomes null (the room host is trusted, a table's JSON less so). */
export function cleanReport(raw: unknown, seats: string[]): { event: "over" | "abandoned"; winner: string | null; turns: number | null; totals: Record<string, number> } | null {
	if (!raw || typeof raw !== "object") return null;
	const r = raw as { event?: unknown; winner?: unknown; turns?: unknown; totals?: unknown };
	if (r.event !== "over" && r.event !== "abandoned") return null;
	const int = (v: unknown, lo: number, hi: number) => (typeof v === "number" && Number.isInteger(v) && v >= lo && v <= hi ? v : null);
	const totals: Record<string, number> = {};
	if (r.totals && typeof r.totals === "object") for (const s of seats) { const n = int((r.totals as Record<string, unknown>)[s], -100_000, 100_000); if (n !== null) totals[s] = n; }
	return { event: r.event, winner: typeof r.winner === "string" && seats.includes(r.winner) ? r.winner : null, turns: int(r.turns, 0, 10_000), totals };
}

/** Is this room still listed? People's tables for six hours; an agent table nobody filled for thirty minutes, and a
 *  finished agent game for ten minutes after its end. */
export function roomListed(r: { created: number; agents?: boolean; humans: string[]; claimed?: string[]; over?: number }, now: number, ttlMs: number): boolean {
	const age = now - r.created;
	if (r.agents && r.over) return now - r.over < AGENT_OVER_LINGER_MS && age < ttlMs;
	if (r.agents && (r.claimed ?? []).length < r.humans.length) return age < AGENT_OPEN_TABLE_TTL_MS;
	return age < ttlMs;
}
