// Mana symbols as small inline pictures: "{2}{R}" is drawn as two discs, and is still the text "{2}{R}".
// 2026-11-13. Presentation only. Nothing on the server changes and no payload changes: a page hands this the text it was about to show and
// puts the returned HTML where the text would have gone.
//   ManaSym.html(escapedText) → HTML    (the text must ALREADY be HTML-escaped by the page; braces survive escaping)
//   ManaSym.text(node)        → the original string back (it is simply node.textContent)
// What each symbol keeps: the original token as real text inside it (visually hidden, so textContent, copy and paste, find-in-page and the
// tests still read "{2}{R}"), and the same token as its accessible name (role="img", aria-label). The drawn letter is CSS generated content,
// not a text node, so it is in neither.
// These are ORIGINAL STAND-IN SYMBOLS drawn here (a coloured disc, a split disc, a bent arrow): no Wizards of the Coast artwork, no icon font,
// nothing downloaded.
//   known     {0}…{20}, {X} {Y} {Z}, {W} {U} {B} {R} {G}, {C}, hybrid {R/W} {2/W} {G/P}, {T} tap, {Q} untap
//   unknown   anything else in braces ({E}, {CHAOS}, {½}, {100}) is left exactly as it was: the literal text
// No page in the logic: tested in node (table/tests/mana_js_test.cjs).   <script src="/assets/mana.js"></script>
(() => {
  const COL = { W: "#f6efd6", U: "#9fc8f0", B: "#b7abb4", R: "#f2a58d", G: "#9fd6ab", C: "#d6d6d6", N: "#cbc5ba", P: "#8a8a8a" };   // N: a number or X
  const RX = /\{([^{}\s]{1,5})\}/g;
  const svg = inner => `<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">${inner}</svg>`;
  const disc = fill => `<circle cx="8" cy="8" r="7.4" fill="${fill}" stroke="#0009" stroke-width=".6"/>`;
  // a disc cut from lower left to upper right: the first colour above the cut, the second below it
  const split = (a, b) => `<path d="M2.77 13.23A7.4 7.4 0 0 1 13.23 2.77Z" fill="${a}"/><path d="M13.23 2.77A7.4 7.4 0 0 1 2.77 13.23Z" fill="${b}"/><circle cx="8" cy="8" r="7.4" fill="none" stroke="#0009" stroke-width=".6"/>`;
  // a bent arrow: three quarters of a ring with a head (turned the other way round for untap)
  const arrow = flip => `<g${flip ? ' transform="matrix(-1 0 0 1 16 0)"' : ""} fill="none" stroke="#141414" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.6 10.4A3.9 3.9 0 1 1 8.4 12"/><path d="M10.3 10.2 8.2 12.1 10.6 13.6"/></g>`;

  // one token (the part between the braces) → {draw: svg innards, glyph: the letter drawn on it} or null when it is not a symbol this knows
  function parse(tok) {
    const t = String(tok).toUpperCase();
    if (/^\d{1,2}$/.test(t) && +t <= 20) return { draw: disc(COL.N), glyph: t };
    if (/^[XYZ]$/.test(t)) return { draw: disc(COL.N), glyph: t };
    if (/^[WUBRGC]$/.test(t)) return { draw: disc(COL[t]), glyph: t };
    if (t === "T") return { draw: disc(COL.N) + arrow(false), glyph: "" };
    if (t === "Q") return { draw: disc(COL.N) + arrow(true), glyph: "" };
    const h = /^([WUBRG2])\/([WUBRGP])$/.exec(t);
    if (h && h[1] !== h[2]) return { draw: split(h[1] === "2" ? COL.N : COL[h[1]], COL[h[2]]), glyph: h[1] + h[2], hybrid: true };
    return null;
  }

  function sym(token) {                                          // "{R}" → its HTML, or the token itself when it is not known
    const m = /^\{([^{}\s]{1,5})\}$/.exec(token), p = m && parse(m[1]);
    if (!p) return token;
    return `<span class="ms${p.hybrid ? " ms-h" : ""}" role="img" aria-label="${token}" data-g="${p.glyph}">${svg(p.draw)}<span class="ms-t">${token}</span></span>`;
  }

  const html = escaped => String(escaped ?? "").replace(RX, tok => sym(tok));
  const tokens = text => (String(text ?? "").match(RX) || []);
  const known = token => sym(token) !== token;

  const CSS = `.ms{position:relative;display:inline-block;width:1.08em;height:1.08em;vertical-align:-.18em;margin:0 .04em;line-height:1;white-space:nowrap}
.ms svg{position:absolute;inset:0;width:100%;height:100%;display:block}
.ms::after{content:attr(data-g);position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font:800 .68em/1 system-ui,-apple-system,"Segoe UI",sans-serif;color:#141414;letter-spacing:0;pointer-events:none}
.ms.ms-h::after{font-size:.46em;letter-spacing:.02em}
.ms-t{position:absolute;left:0;top:0;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;font-size:1px;opacity:0}`;
  // ── on a page: every text node that holds a known symbol is redrawn, wherever the page put it (a hand, a menu, a choice, the log, a tooltip) ──
  // The pages build their HTML in many places; rather than change each one, the text is repainted after it lands. Left alone on purpose: form
  // fields and <option> (they cannot hold pictures: a dropdown keeps "{2}{R}" as text), <script>/<style>, anything editable, attributes such as
  // title="…", and anything inside [data-mana="off"]. A symbol already drawn is never drawn twice.
  const SKIP = /^(SCRIPT|STYLE|TEXTAREA|INPUT|SELECT|OPTION|NOSCRIPT|TITLE|svg|CODE|PRE)$/;
  function paint(root) {
    if (typeof document === "undefined" || !root) return 0;
    const start = root.nodeType === 3 ? root.parentNode : root; if (!start || start.nodeType !== 1 && start.nodeType !== 11 && start.nodeType !== 9) return 0;
    const bad = el => { for (let e = el; e && e.nodeType === 1; e = e.parentNode) if (SKIP.test(e.nodeName) || e.isContentEditable || (e.classList && e.classList.contains("ms")) || (e.dataset && e.dataset.mana === "off")) return true; return false; };
    const todo = [], w = document.createTreeWalker(root.nodeType === 3 ? root.parentNode : root, 4 /* text */);
    for (let n = root.nodeType === 3 ? root : w.nextNode(); n; n = root.nodeType === 3 ? null : w.nextNode()) {
      const v = n.nodeValue; if (!v || v.indexOf("{") < 0 || !tokens(v).some(known) || bad(n.parentNode)) continue;
      todo.push(n);
    }
    for (const n of todo) { const tpl = document.createElement("template");
      tpl.innerHTML = html(n.nodeValue.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])));
      n.parentNode.replaceChild(tpl.content, n); }
    return todo.length;
  }
  let obs = null;
  function watch(root) {                                          // repaint whatever the page adds or rewrites from now on
    if (typeof MutationObserver === "undefined" || obs) return;
    const target = root || document.body; paint(target);
    obs = new MutationObserver(list => { for (const m of list) { if (m.type === "characterData") paint(m.target); else m.addedNodes.forEach(n => { if (n.nodeType === 1 || n.nodeType === 3) paint(n); }); } });
    obs.observe(target, { childList: true, subtree: true, characterData: true });
  }
  if (typeof document !== "undefined" && document.head && !document.getElementById("mana-sym-css")) {
    const st = document.createElement("style"); st.id = "mana-sym-css"; st.textContent = CSS; document.head.appendChild(st);
  }
  if (typeof document !== "undefined" && typeof addEventListener === "function") {
    if (document.body) watch(); else addEventListener("DOMContentLoaded", () => watch());
  }
  window.ManaSym = { html, sym, tokens, known, parse, paint, watch, text: node => (node && node.textContent) || "", CSS };
})();
