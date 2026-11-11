// Which speech voice each AI seat speaks with. Shared by every page that talks (table, voice, show, xr).
//
// The old pages picked `englishVoices[(i * 3) % n]`, and on macOS 19 of the 48 English voices are novelty or legacy ones (Albert,
// Zarvox, Bubbles, Trinoids, Bad News, Fred…): an AI could sound like a robot or a pipe organ by accident. Now:
//   1. a voice the AI DECLARED (POST /api/identity {"voice": …}) is honoured exactly, novelty or not: how an AI presents itself
//      is its call;
//   2. otherwise the most natural English voice still free (Premium/Enhanced/Siri/Google first, then the standard
//      voices, then the older synthetic ones), a different one per seat; novelty voices are never picked by default;
//   3. pace and pitch stay at 1 unless the AI asked for something else (clamped to a human range: no chipmunks).
//
//   <script src="/assets/voices.js"></script>  →  TableVoices.voicePlan(players, speechSynthesis.getVoices())

(function () {
  var NOVELTY = /^(Albert|Bad News|Bahh|Bells|Boing|Bubbles|Cellos|Wobble|Good News|Jester|Organ|Superstar|Trinoids|Whisper|Zarvox|Hysterical|Deranged|Pipe Organ)\b/i;
  var LEGACY = /^(Fred|Junior|Ralph|Kathy|Victoria|Agnes|Princess|Vicki|Bruce)\b/i;            // 1990s MacInTalk: robotic
  var ELOQUENCE = /^(Eddy|Flo|Grandma|Grandpa|Reed|Rocko|Sandy|Shelley)\b/i;               // synthetic, but intelligible

  function isNovelty(v) { return NOVELTY.test(v.name || ""); }

  function naturalness(v) {
    const n = v.name || "";
    if (NOVELTY.test(n)) return -100;
    let s = 0;
    if (/premium|enhanced|neural|natural|siri/i.test(n)) s += 50;
    if (/^Google\b/i.test(n)) s += 30;
    if (/^Microsoft\b.*Online/i.test(n)) s += 30;
    if (LEGACY.test(n)) s -= 40;
    if (ELOQUENCE.test(n)) s -= 20;
    if (v.localService === false) s -= 2;          // network voices can lag; a small nudge only
    if (/^en[-_](US|GB|AU|IE|IN|ZA|NZ|CA)/i.test(v.lang || "")) s += 5;
    return s;
  }

  var clamp = (x, lo, hi, d) => (typeof x === "number" && isFinite(x) ? Math.min(hi, Math.max(lo, x)) : d);

  // players: [{name, voice?, voice_rate?, voice_pitch?}] in seat order; voices: speechSynthesis.getVoices()
  // → {name: {voice, rate, pitch, declared}}
  function voicePlan(players, voices) {
    const en = voices.filter(v => /^en/i.test(v.lang || ""));
    const used = new Set(), plan = {};
    const byName = want => voices.find(v => v.name === want) || voices.find(v => (v.name || "").startsWith(want + " ")) ||
      voices.find(v => (v.name || "").toLowerCase().includes(String(want).toLowerCase()));
    for (const p of players) {                               // declared voices first: they are the AI's own choice
      if (!p.voice) continue;
      const v = byName(p.voice);
      if (v) { plan[p.name] = { voice: v, declared: true }; used.add(v.name); }
    }
    const pool = en.filter(v => !isNovelty(v)).sort((a, b) => naturalness(b) - naturalness(a));
    for (const p of players) {
      if (plan[p.name]) continue;
      const v = pool.find(x => !used.has(x.name)) || pool[0] || null;   // distinct while the pool lasts
      if (v) used.add(v.name);
      plan[p.name] = { voice: v, declared: false };
    }
    for (const p of players) {
      plan[p.name].rate = clamp(p.voice_rate, 0.8, 1.25, 1);
      plan[p.name].pitch = clamp(p.voice_pitch, 0.8, 1.2, 1);
    }
    return plan;
  }

  function voiceFor(plan, who) { return (plan && plan[who]) || { voice: null, rate: 1, pitch: 1 }; }

  const api = { voicePlan, voiceFor, naturalness, isNovelty };
  if (typeof window !== "undefined") window.TableVoices = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;      // node tests
})();
