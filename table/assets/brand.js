// Divinci Table brand mark: the relic sigil and the name, bottom corner. Links to the lobby in the cloud
// (where "/" is the lobby) and nowhere on a laptop table.
(() => {
  const a = document.createElement(location.hostname.endsWith("workers.dev") || location.hostname.endsWith("divinci.ai") ? "a" : "span");
  a.className = "dt-mark"; if (a.tagName === "A") { a.href = "/"; a.title = "Back to the lobby"; }
  a.innerHTML = '<svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="44" fill="none" stroke="#d9b46a" stroke-width="5"/>' +
    '<path d="M50 16l29 17v34L50 84 21 67V33z" fill="none" stroke="#d9b46a" stroke-width="5"/><circle class="core" cx="50" cy="50" r="11" fill="#7ff3ea"/></svg>' +
    "<span>DIVINCI TABLE</span>";
  document.body.appendChild(a);
})();
