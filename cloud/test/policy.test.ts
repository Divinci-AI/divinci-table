// The lobby's AI and admin rules (src/policy.ts).  node --experimental-strip-types --test cloud/test/policy.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { aiKeyAllowed, aiRoomRefusal, CODE_FAILS_GLOBAL_PER_HOUR, CODE_FAILS_PER_HOUR, isAdmin, sameText } from "../src/policy.ts";

const ok = { configured: true, codeOk: true, failsHere: 0, failsEverywhere: 0, off: false, today: 0, perDay: 4 };

test("sameText: equal strings match, anything else doesn't", async () => {
	assert.equal(await sameText("amber-anvil", "amber-anvil"), true);
	assert.equal(await sameText("amber-anvil", "amber-anvi1"), false);
	assert.equal(await sameText("", "amber-anvil"), false);
	assert.equal(await sameText("amber-anvil ", "amber-anvil"), false);
});

test("isAdmin: needs the exact bearer token, and no token configured means nobody", async () => {
	assert.equal(await isAdmin("Bearer s3cret", "s3cret"), true);
	assert.equal(await isAdmin("Bearer wrong", "s3cret"), false);
	assert.equal(await isAdmin("s3cret", "s3cret"), false);
	assert.equal(await isAdmin(null, "s3cret"), false);
	assert.equal(await isAdmin("Bearer ", ""), false);
	assert.equal(await isAdmin("Bearer undefined", undefined), false);
});

test("an AI room with everything right starts", () => assert.equal(aiRoomRefusal(ok), null));

test("not configured: no AI at all", () => assert.match(aiRoomRefusal({ ...ok, configured: false }) ?? "", /aren't available/));

test("a wrong code is refused", () => assert.match(aiRoomRefusal({ ...ok, codeOk: false }) ?? "", /invitation/));

test("too many wrong codes locks the address out — even with the right code (a guesser learns nothing)", () => {
	assert.match(aiRoomRefusal({ ...ok, failsHere: CODE_FAILS_PER_HOUR }) ?? "", /Too many/);
	assert.match(aiRoomRefusal({ ...ok, failsEverywhere: CODE_FAILS_GLOBAL_PER_HOUR }) ?? "", /Too many/);
	assert.equal(aiRoomRefusal({ ...ok, failsHere: CODE_FAILS_PER_HOUR - 1 }), null);
});

test("the kill switch and the daily budget refuse", () => {
	assert.match(aiRoomRefusal({ ...ok, off: true }) ?? "", /paused/);
	assert.match(aiRoomRefusal({ ...ok, today: 4 }) ?? "", /all taken/);
	assert.equal(aiRoomRefusal({ ...ok, today: 3 }), null);
});

test("a waking room gets the AI key only with AI seats and the switch on", () => {
	assert.equal(aiKeyAllowed(true, false), true);
	assert.equal(aiKeyAllowed(true, true), false);
	assert.equal(aiKeyAllowed(false, false), false);
});

test("brute force is impractical: a 5-word code from 64 words at 8 guesses an hour", () => {
	const space = 64 ** 5, perYear = CODE_FAILS_PER_HOUR * 24 * 365;
	assert.ok(space / perYear > 10_000, `${space / perYear} years`);
});
