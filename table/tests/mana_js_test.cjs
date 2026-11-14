// assets/mana.js on its own: no browser, no table. Run: node table/tests/mana_js_test.cjs
// Every known symbol becomes a small picture that still IS its text: stripping the tags from the HTML gives back the original string exactly
// (that is what textContent, copy and the other tests read), and each picture's accessible name is its token. Unknown symbols stay literal.
const fs = require("fs"), path = require("path"), vm = require("vm");
const win = {}; win.window = win;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "..", "assets", "mana.js"), "utf8"), win);
const M = win.ManaSym;
let ok = true; const check = (n, c, d) => { console.log((c ? "PASS " : "FAIL ") + n + (c ? "" : "  " + JSON.stringify(d))); ok = ok && !!c; };
const strip = h => h.replace(/<[^>]+>/g, "");                       // what textContent would be (the page escaped the text before; nothing here un-escapes)
const labels = h => [...h.matchAll(/aria-label="([^"]*)"/g)].map(m => m[1]);
const pics = h => (h.match(/<span class="ms[ "]/g) || []).length;
const glyphs = h => [...h.matchAll(/data-g="([^"]*)"/g)].map(m => m[1]);

check("the API is there (and it loads without a page)", M && ["html", "sym", "tokens", "known", "parse"].every(k => typeof M[k] === "function"));

// generic numbers
for (const n of [0, 1, 2, 3, 7, 10, 15, 20]) { const t = `{${n}}`, h = M.html(t);
  check(`${t}: one picture, drawn with "${n}", named ${t}, text ${t}`, pics(h) === 1 && glyphs(h)[0] === String(n) && labels(h)[0] === t && strip(h) === t, h); }
// X (and Y, Z)
for (const x of ["X", "Y", "Z"]) { const t = `{${x}}`, h = M.html(t); check(`${t}: a picture with "${x}"`, pics(h) === 1 && glyphs(h)[0] === x && labels(h)[0] === t && strip(h) === t, h); }
// each colour, and colourless
const fill = h => (/<circle[^>]*fill="(#[0-9a-f]+)"/.exec(h) || [])[1];
const seen = new Set();
for (const c of ["W", "U", "B", "R", "G", "C"]) { const t = `{${c}}`, h = M.html(t); seen.add(fill(h));
  check(`${t}: a disc with "${c}", named ${t}, text ${t}`, pics(h) === 1 && glyphs(h)[0] === c && labels(h)[0] === t && strip(h) === t && !!fill(h), h); }
check("the five colours and colourless are six different discs, none the colour of a number", seen.size === 6 && !seen.has(fill(M.html("{2}"))), [...seen]);
// hybrid
for (const t of ["{R/W}", "{G/U}", "{2/W}", "{B/P}"]) { const h = M.html(t), fills = [...h.matchAll(/<path[^>]*fill="(#[0-9a-f]+)"/g)].map(m => m[1]);
  check(`${t}: ONE split disc (two halves, two colours), named ${t}, text ${t}`, pics(h) === 1 && fills.length === 2 && fills[0] !== fills[1] && labels(h)[0] === t && strip(h) === t && /ms-h/.test(h), h); }
check("{R/W} is cut from the colours of {R} and {W}", [...M.html("{R/W}").matchAll(/<path[^>]*fill="(#[0-9a-f]+)"/g)].map(m => m[1]).join() === [fill(M.html("{R}")), fill(M.html("{W}"))].join());
// tap, untap
for (const t of ["{T}", "{Q}"]) { const h = M.html(t); check(`${t}: a disc with an arrow and no letter, named ${t}, text ${t}`, pics(h) === 1 && glyphs(h)[0] === "" && /stroke-linecap/.test(h) && labels(h)[0] === t && strip(h) === t, h); }
check("tap and untap are drawn differently (the arrow turns the other way)", M.html("{T}") !== M.html("{Q}").replace("{Q}", "{T}").replace("{Q}", "{T}"));
// unknown symbols: the literal text, untouched
for (const t of ["{E}", "{CHAOS}", "{½}", "{100}", "{21}", "{R/R}", "{W/U/B}", "{}", "{ R }", "{TK}", "{S}"]) check(`${t} is not a symbol this draws: it stays the literal text`, M.html(t) === t && !M.known(t), M.html(t));
check("lower case is read the same and keeps the text as written", pics(M.html("{r}")) === 1 && strip(M.html("{r}")) === "{r}" && labels(M.html("{r}"))[0] === "{r}");

// whole strings, as the pages show them
const cases = ["{2}{R}", "{1}{B}", "{3}{B}{B}", "{X}{R}{R}", "{R/W}{R/W}", "Equip {3}", "{T}: Add {B} or {G}.", "{2}, {T}, Sacrifice this artifact: You gain 3 life.",
  "Stray from History {1}{B} (Sorcery)", "Moopsy {G} (Creature 1/1)", "{2}: This creature gains reach until end of turn.", "needs {3}: you have 1 untapped mana source",
  "no symbols here at all", "", "an {E} energy counter and {2}{U}", "Beam me up {3}{U} (You may cast this card from your graveyard.)", "pay {C}{C} &amp; {1} &lt;now&gt;"];
for (const s of cases) { const h = M.html(s), want = M.tokens(s).filter(M.known);
  check(`text kept exactly: ${JSON.stringify(s).slice(0, 60)}`, strip(h) === s && same(labels(h), want) && pics(h) === want.length, [h, want]); }
function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
check("'{2}{R}' is two pictures in order", same(labels(M.html("{2}{R}")), ["{2}", "{R}"]) && same(glyphs(M.html("{2}{R}")), ["2", "R"]));
check("text around the symbols is not touched (already-escaped text stays escaped once)", M.html("a &lt;b&gt; {1} &amp; c").startsWith("a &lt;b&gt; <span") && M.html("a &lt;b&gt; {1} &amp; c").endsWith(" &amp; c"));
check("the letter on a disc is NOT text in the document (CSS draws it from data-g): the only text inside a picture is its token", [...M.html("{2}{R}{T}{R/W}").matchAll(/<span class="ms[^>]*>(.*?)<\/span><\/span>/g)].every(m => strip(m[1]) === labels(m[0])[0]));
check("every picture is role=img with its svg hidden from assistive tech (the name is read once)", [...M.html("{2}{R}{T}").matchAll(/<span class="ms[ "][^>]*>/g)].every(m => /role="img"/.test(m[0])) && (M.html("{2}{R}{T}").match(/aria-hidden="true"/g) || []).length === 3);
check("null and undefined are the empty string", M.html(null) === "" && M.html(undefined) === "");
check("no file is fetched and nothing remote is named: the pictures are inline SVG", !/https?:|url\(|@font-face|<image|<use/.test(fs.readFileSync(path.join(__dirname, "..", "assets", "mana.js"), "utf8").replace(/\/\/.*$/gm, "")));

console.log(ok ? "\nall passed" : "\nFAILED");
process.exit(ok ? 0 : 1);
