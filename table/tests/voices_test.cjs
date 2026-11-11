// assets/voices.js: no AI gets a novelty voice by accident; a voice an AI declares is honoured (even a novelty one);
// seats get different voices; pace and pitch stay human. Uses this Mac's real `say -v ?` list (voices_mac_fixture.json)
// plus Chrome's Google voices.        node table/tests/voices_test.cjs
const V = require("../assets/voices.js");
const mac = require("./voices_mac_fixture.json");
let failed = 0;
const check = (n, ok, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + d}`); if (!ok) failed++; };
const players = [{ name: "Claude" }, { name: "Gemma" }, { name: "Michael" }, { name: "Sam" }];
let plan = V.voicePlan(players, mac);
const names = players.map(p => plan[p.name].voice && plan[p.name].voice.name);
check("no seat gets a novelty voice by default (this Mac's list)", names.every(n => n && !V.isNovelty({ name: n })), names.join(", "));
check("no seat gets an old MacInTalk robot voice when better ones exist", names.every(n => !/^(Fred|Junior|Ralph|Kathy|Albert)\b/.test(n)), names.join(", "));
check("every seat gets a different voice", new Set(names).size === names.length, names.join(", "));
check("pace and pitch default to 1 (no distortion)", players.every(p => plan[p.name].rate === 1 && plan[p.name].pitch === 1));
const old = mac.filter(v => /^en/.test(v.lang));
const oldPicks = players.map((p, i) => old[(i * 3) % old.length].name);
console.log("    (the old rule picked: " + oldPicks.join(", ") + ")");
plan = V.voicePlan([{ name: "Claude", voice: "Zarvox" }, { name: "Gemma" }], mac);
check("an AI that DECLARES a novelty voice gets it (its choice)", plan.Claude.voice.name === "Zarvox" && plan.Claude.declared);
check("…and the others still get natural ones", !V.isNovelty(plan.Gemma.voice));
plan = V.voicePlan([{ name: "A", voice: "Moira" }, { name: "B" }, { name: "C" }], mac);
check("a declared voice is matched by its short name and not reused for another seat", /^Moira/.test(plan.A.voice.name) && !/^Moira/.test(plan.B.voice.name) && !/^Moira/.test(plan.C.voice.name),
      [plan.A.voice.name, plan.B.voice.name, plan.C.voice.name].join(", "));
plan = V.voicePlan([{ name: "A", voice_rate: 3, voice_pitch: 0.1 }, { name: "B", voice_rate: 1.1 }], mac);
check("asked-for pace and pitch are clamped to a human range", plan.A.rate === 1.25 && plan.A.pitch === 0.8 && plan.B.rate === 1.1);
const chrome = [...mac, { name: "Google US English", lang: "en-US", localService: false }, { name: "Google UK English Female", lang: "en-GB", localService: false }];
plan = V.voicePlan([{ name: "A" }, { name: "B" }], chrome);
check("in Chrome, the Google voices rank first", /^Google/.test(plan.A.voice.name) && /^Google/.test(plan.B.voice.name), plan.A.voice.name + ", " + plan.B.voice.name);
plan = V.voicePlan([{ name: "A" }], []);
check("no voices at all: no crash, no voice", plan.A.voice === null);
console.log(failed ? `\n${failed} FAILED` : "\nall passed");
process.exit(failed ? 1 : 0);
