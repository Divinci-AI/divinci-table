// A port for a browser test's own table server.
// The live tables use 8800, 8801, 8820, 8830 and 8883 (and a full-suite run restarts 8800), so a test must
// never land on one of them: a test that binds a live port either fails to start or, worse, talks to a real game.
// The range 9100-9899 holds none of them; the reserved set is still checked so a future edit to the range can't
// quietly bring them back.
const RESERVED = new Set([8800, 8801, 8820, 8830, 8883]);

function testPort(lo = 9100, span = 800) {
  for (;;) {
    const p = lo + Math.floor(Math.random() * span);
    if (!RESERVED.has(p)) return p;
  }
}

module.exports = { testPort, RESERVED };
