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
  #artbox img{max-width:92vw;max-height:92vh;border-radius:14px;box-shadow:0 10px 40px #000}`;
  document.head.appendChild(css);
  window.CardArt = {
    has: n => !!A[n],
    src: n => A[n] ? `/art/${A[n]}.jpg` : "",
    img: (n, cls) => A[n] ? `<img class="cardimg ${cls || ""}" src="/art/${A[n]}.jpg" loading="lazy" alt="${attr(n)}" data-art="${A[n]}">` : "",
  };
  addEventListener("DOMContentLoaded", () => {
    const box = document.createElement("div"); box.id = "artbox"; box.innerHTML = "<img alt=''>"; document.body.appendChild(box);
    box.onclick = () => { box.style.display = "none"; };
    document.addEventListener("click", e => {
      const im = e.target.closest && e.target.closest("img.cardimg"); if (!im) return;
      e.stopPropagation(); e.preventDefault();
      if (window.__artChips && window.__artChips(im)) return;          // a page that knows this card's state shows it under the art
      box.firstChild.src = `/art/${im.dataset.art}.jpg`; box.style.display = "flex";
    }, true);
  });
})();
