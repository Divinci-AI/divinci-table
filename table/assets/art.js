// Card art on the game pages. /art/index.js maps a card name to an image id; the images are served from TABLE_ART_DIR, outside
// the repo (card art is copyrighted: never commit it). CardArt.img(name, cls) gives an <img> (or ""); clicking one opens it large.
(function () {
  "use strict";
  const A = window.CARD_ART || {};
  const attr = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = document.createElement("style");
  css.textContent = `.cardimg{width:84px;border-radius:6px;float:left;margin:0 10px 6px 0;cursor:zoom-in;display:block}
  .cardimg.sm{width:52px;margin:0 6px 4px 0}.cardimg.bd{float:none;width:100%;margin:0 0 4px;border-radius:5px}
  .artrow{overflow:auto;margin-top:4px}.artrow .cardimg{display:inline-block;float:none}
  #artbox{position:fixed;inset:0;z-index:10000;background:#000c;display:none;align-items:center;justify-content:center;cursor:zoom-out}
  #artbox img{max-width:92vw;max-height:92vh;border-radius:14px;box-shadow:0 10px 40px #000}
  /* the same box as a PEEK: beside the thing pointed at, never over it, and it takes no clicks (2026-11-13: read a card before choosing it) */
  #artbox.peek{inset:auto;background:none;display:none;cursor:default;pointer-events:none;z-index:10001;align-items:flex-start;justify-content:flex-start}
  #artbox.peek img{max-width:none;max-height:none;width:100%;height:100%;object-fit:contain}
  #artface{display:none;box-sizing:border-box;width:min(92vw,380px);aspect-ratio:5/7;overflow:auto;padding:16px 16px 44px;border-radius:14px;position:relative;
    background:linear-gradient(#f4ecd6,#e7dcbd);color:#1b1710;border:6px solid #17130c;box-shadow:0 10px 40px #000;font:15px/1.38 Georgia,"Times New Roman",serif;text-align:left}
  #artbox.peek #artface{width:100%;height:100%;aspect-ratio:auto;overflow:hidden;font-size:12.5px;padding:10px 11px 30px;border-width:4px}
  #artface .fn{display:flex;justify-content:space-between;gap:8px;font:700 1.12em/1.2 system-ui,sans-serif;border-bottom:1px solid #0003;padding-bottom:5px}
  #artface .fty{font:600 .9em system-ui,sans-serif;margin:7px 0;padding:3px 0;border-bottom:1px solid #0003}
  #artface .ftx{white-space:pre-wrap}#artface .fpt{position:absolute;right:10px;bottom:8px;font:800 1.15em system-ui,sans-serif;background:#17130c;color:#f4ecd6;border-radius:8px;padding:2px 9px}
  #artface .fno{opacity:.7;font-style:italic}
  .peekbtn{flex:0 0 auto;min-width:40px;min-height:36px;padding:2px 8px;margin:6px 0 0 6px;border-radius:8px;border:1px solid #d9b46a88;background:#1b2a33;color:#f4dc9b;font:600 14px system-ui;cursor:pointer}
  .peekrow{display:flex;align-items:stretch}.peekrow>button:first-child{flex:1 1 auto;min-width:0}`;
  document.head.appendChild(css);
  // ── the whole card, for reading before choosing ──
  // A card's words come from /api/card?name=…: the table's public Oracle lookup, by NAME, for any card that exists. It knows nothing about the
  // game (no zone, no owner), so asking it can only tell you what a card you can already name says: never what is in a hand or a library.
  const INFO = new Map();
  function info(name) {
    if (!name) return Promise.resolve(null);
    if (!INFO.has(name)) INFO.set(name, fetch("/api/card?name=" + encodeURIComponent(name)).then(r => r.ok ? r.json() : null).then(d => d && d.type != null ? { name, ...d } : null).catch(() => { INFO.delete(name); return null; }));
    return INFO.get(name);
  }
  // What the page was ALREADY sent about a card (a permanent's text in the seat's state or on the public board, a card of a hand this seat was
  // shown) is used first: know() puts it in front of the lookup. Only cards with a type line or text count as known.
  function know(c) { if (c && c.name && (c.type || c.text) && !KNOWN.has(c.name)) KNOWN.set(c.name, { name: c.name, cost: c.cost || "", type: c.type || "", text: c.text || "",
    power: c.power ?? (c.pt ? String(c.pt).split("/")[0] : null), toughness: c.toughness ?? (c.pt ? String(c.pt).split("/")[1] : null) }); }
  const KNOWN = new Map();
  // a text-only card face: name and cost, type line, rules text, power/toughness. Used when the table has no art for the card.
  function face(c) {
    if (!c) return `<div class="fn"><span></span></div><div class="ftx fno">The table has no text for this card.</div>`;
    const pt = c.power != null && c.power !== "" && c.toughness != null && c.toughness !== "" ? `${c.power}/${c.toughness}` : "";
    return `<div class="fn"><span>${attr(c.name)}</span><span>${attr(c.cost || "")}</span></div><div class="fty">${attr(c.type || "")}</div><div class="ftx">${attr(c.text || "")}</div>${pt ? `<div class="fpt">${attr(pt)}</div>` : ""}`;
  }
  // Where a w×h preview goes so that it does NOT cover the anchor (the option being chosen): right of it, left of it, below, above; when
  // none fits whole, the roomier of above / below, shrunk to fit. All in viewport pixels. → {left, top, w, h, side}
  function place(a, w, h, vw, vh, m = 10) {
    const clampY = t => Math.max(m, Math.min(t, vh - h - m)), clampX = l => Math.max(m, Math.min(l, vw - w - m));
    if (a.right + m + w + m <= vw && h + 2 * m <= vh) return { left: a.right + m, top: clampY(a.top + (a.bottom - a.top) / 2 - h / 2), w, h, side: "right" };
    if (a.left - m - w - m >= 0 && h + 2 * m <= vh) return { left: a.left - m - w, top: clampY(a.top + (a.bottom - a.top) / 2 - h / 2), w, h, side: "left" };
    const below = vh - a.bottom - 2 * m, above = a.top - 2 * m;
    if (below >= h) return { left: clampX(a.left), top: a.bottom + m, w, h, side: "below" };
    if (above >= h) return { left: clampX(a.left), top: a.top - m - h, w, h, side: "above" };
    const room = Math.max(below, above, 60), k = Math.min(1, room / h), w2 = Math.round(w * k), h2 = Math.round(h * k);
    const l2 = Math.max(m, Math.min(a.left, vw - w2 - m));
    return below >= above ? { left: l2, top: a.bottom + m, w: w2, h: h2, side: "below" } : { left: l2, top: Math.max(m, a.top - m - h2), w: w2, h: h2, side: "above" };
  }
  let box = null, faceEl = null, peekName = "", peekSeq = 0;
  function fill(name) {                                          // the art when the table has it, else the text face (asked for by name)
    const src = A[name] ? `/art/${A[name]}.jpg` : "", im = box.firstChild, seq = ++peekSeq;
    if (src) { im.src = src; im.alt = name; im.style.display = ""; faceEl.style.display = "none"; return; }
    im.removeAttribute("src"); im.style.display = "none"; faceEl.style.display = "block"; faceEl.dataset.card = name;
    faceEl.innerHTML = face({ name, type: "…", text: "" });
    const done = c => { if (seq !== peekSeq) return; faceEl.innerHTML = face(c ? c : null); if (!c) faceEl.querySelector(".fn span").textContent = name; };
    if (KNOWN.has(name)) { const k = KNOWN.get(name); done(k); if (!k.cost) info(name).then(c => { if (c && c.cost && seq === peekSeq) done({ ...k, cost: c.cost }); }); return; }
    info(name).then(done);
  }
  function unpeek() { if (!box || !box.classList.contains("peek")) return; box.classList.remove("peek"); box.style.cssText = ""; box.style.display = "none"; box.firstChild.style.display = ""; faceEl.style.display = "none"; peekName = ""; }
  function peek(name, anchor) {                                   // beside `anchor` (an element), never over it; takes no clicks
    if (!box || !name || !anchor || !anchor.getBoundingClientRect) return false;
    if (box.style.display === "flex" && !box.classList.contains("peek")) return false;      // the large view is open: leave it
    const r = anchor.getBoundingClientRect(), w = Math.round(Math.min(300, innerWidth * 0.62)), h = Math.round(w * 1.4);
    const p = place({ left: r.left, top: r.top, right: r.right, bottom: r.bottom }, w, h, innerWidth, innerHeight);
    const old = box.querySelector("#artinfo"); if (old) old.remove();
    box.classList.add("peek"); box.style.cssText = `display:flex;left:${p.left}px;top:${p.top}px;width:${p.w}px;height:${p.h}px`; box.dataset.side = p.side;
    if (peekName !== name) { peekName = name; fill(name); }
    box.setAttribute("aria-label", "Card: " + name);
    return true;
  }
  function show(name) {                                           // the large view (a tap anywhere closes it): art, or the text face
    if (!box || !name) return false;
    unpeek(); const old = box.querySelector("#artinfo"); if (old) old.remove();
    box.style.flexDirection = ""; fill(name); box.style.display = "flex"; return true;
  }
  window.CardArt = {
    has: n => !!A[n],
    src: n => A[n] ? `/art/${A[n]}.jpg` : "",
    img: (n, cls) => A[n] ? `<img class="cardimg ${cls || ""}" src="/art/${A[n]}.jpg" loading="lazy" alt="${attr(n)}" data-art="${A[n]}">` : "",
    info, face, place, peek, unpeek, show, know,
    // a "view" button for touch (and for anyone): <button data-peekbtn="Card Name">
    viewBtn: n => n ? `<button type="button" class="peekbtn" data-peekbtn="${attr(n)}" aria-label="View ${attr(n)}" title="View the whole card">👁</button>` : "",
    selBtn: () => `<button type="button" class="peekbtn" data-peekbtn="" aria-label="View the selected card" title="View the whole card">👁</button>`,
    // the card a label names, when the page has no better way to know: "Horta {4}{R}{R} (Creature 7/6)", "Horta (to your hand)". Asked of the
    // public lookup by name; a label that names no card gets no preview. → Promise<name | "">
    guess: async label => { const l = String(label || "").trim(); if (!l || l.length > 90 || !/^[A-Z0-9"']/.test(l)) return "";
      for (const c of [...new Set([l, l.split(" {")[0], l.split(" (")[0], l.split(": ")[0]])]) if (c && c.length <= 60 && await info(c.trim())) return c.trim();
      return ""; },
  };
  addEventListener("DOMContentLoaded", () => {
    box = document.createElement("div"); box.id = "artbox"; box.innerHTML = "<img alt=''><div id='artface'></div>"; document.body.appendChild(box);
    faceEl = box.querySelector("#artface");
    box.onclick = () => { box.style.display = "none"; };
    // The peek: anything marked data-peek="Card Name" shows that card beside itself while the mouse is over it or the keyboard focus is on it.
    // A dropdown marked data-peeksel shows the card of the option it currently has (a browser's open list cannot be hovered item by item).
    // On touch: press and hold (half a second) shows it and uses up the tap that follows, so holding never CHOOSES; the next tap puts it away.
    // The peek takes no pointer events and is placed off the element, so the option under it stays pressable.
    const nameOf = el => { if (!el || !el.closest) return ["", null]; const s = el.closest("select[data-peeksel]");
      if (s) { const o = s.options[s.selectedIndex]; return [(o && o.dataset.peek) || "", s]; }
      const p = el.closest("[data-peek]"); return p && p.nodeName !== "OPTION" ? [p.dataset.peek || "", p] : ["", null]; };
    let hold = 0, held = false, pinned = false;
    document.addEventListener("pointerover", e => { if (e.pointerType !== "mouse" || pinned) return; const [n, el] = nameOf(e.target); if (n) peek(n, el); });
    document.addEventListener("pointerout", e => { if (e.pointerType !== "mouse" || pinned) return; const [n, el] = nameOf(e.target); if (n && !(e.relatedTarget && el.contains(e.relatedTarget))) unpeek(); });
    document.addEventListener("focusin", e => { const [n, el] = nameOf(e.target); if (n) peek(n, el); });
    document.addEventListener("focusout", e => { const [n] = nameOf(e.target); if (n && !pinned) unpeek(); });
    document.addEventListener("change", e => { const [n, el] = nameOf(e.target); if (el && el.nodeName === "SELECT") { if (n) peek(n, el); else unpeek(); } });
    document.addEventListener("pointerdown", e => {
      if (pinned) { pinned = false; unpeek(); }
      if (e.pointerType === "mouse") return; const [n, el] = nameOf(e.target); if (!n || el.nodeName === "SELECT") return;
      const x = e.clientX, y = e.clientY; held = false; clearTimeout(hold);
      hold = setTimeout(() => { held = true; pinned = true; peek(n, el); }, 500);
      const off = ev => { if (ev.type === "pointermove" && Math.hypot(ev.clientX - x, ev.clientY - y) < 10) return; clearTimeout(hold);
        document.removeEventListener("pointermove", off, true); document.removeEventListener("pointerup", off, true); document.removeEventListener("pointercancel", off, true); };
      document.addEventListener("pointermove", off, true); document.addEventListener("pointerup", off, true); document.addEventListener("pointercancel", off, true);
    }, true);
    document.addEventListener("contextmenu", e => { if (held || (nameOf(e.target)[0] && e.pointerType && e.pointerType !== "mouse")) e.preventDefault(); }, true);
    document.addEventListener("keydown", e => { if (e.key === "Escape" && box.classList.contains("peek")) { pinned = false; unpeek(); } });
    document.addEventListener("click", e => {
      if (held) { held = false; e.stopImmediatePropagation(); e.preventDefault(); return; }      // the tap that ends a press-and-hold chooses nothing
      const b = e.target.closest && e.target.closest("[data-peekbtn]"); if (!b) return;
      e.stopImmediatePropagation(); e.preventDefault();                                         // the "view" button: the large view; it never chooses
      let n = b.dataset.peekbtn;                                                                 // (empty: the card of the dropdown beside it, as it stands)
      if (!n) { const s = b.parentNode && b.parentNode.querySelector("select[data-peeksel]"), o = s && s.options[s.selectedIndex]; n = (o && o.dataset.peek) || ""; }
      if (n) show(n);
    }, true);
    addEventListener("scroll", () => { if (!pinned) unpeek(); }, true);
    document.addEventListener("click", e => {
      const im = e.target.closest && e.target.closest("img.cardimg"); if (!im) return;
      e.stopPropagation(); e.preventDefault();
      if (window.__artChips && window.__artChips(im)) return;          // a page that knows this card's state shows it under the art
      unpeek(); faceEl.style.display = "none"; box.firstChild.style.display = "";
      box.firstChild.src = `/art/${im.dataset.art}.jpg`; box.style.display = "flex";
    }, true);
  });
})();
