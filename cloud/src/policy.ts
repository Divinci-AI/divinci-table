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
