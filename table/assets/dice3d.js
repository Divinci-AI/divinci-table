// The opening ceremony, seen: when the table's high roll for who goes first settles, a d20 per player is thrown onto a felt table
// (three.js, the vendored build), lands, and shows its number; a tie throws again for the tied players; then "<name> goes first".
// Include with <script src="/assets/dice3d.js" defer></script> on any page; it follows the event stream by itself and does nothing
// until a high roll ends. ?nodice=1 turns it off for a tab (test games).
// The numbers come from the server (the roll is already decided and logged): the dice are drawn to land on them, never to decide them.
(function () {
  "use strict";
  if (window.__dice3d) return; window.__dice3d = true;
  if (new URLSearchParams(location.search).get("nodice") === "1") return;
  const COL = { Michael: 0x5fd3ff, Sam: 0xb58cff, Claude: 0xf0a868, Fusion: 0xd8b45a, Sonnet: 0xf0a868, Opus: 0x5fd3ff };
  const FALLBACK = [0xe6e6e6, 0x9be07a, 0xff8f8f, 0xffe27a];
  let T = null, cursor = null, busy = false;
  const queue = [];

  async function three() { return T || (T = await import("/vendor/three.module.min.js")); }

  function overlay() {
    const el = document.createElement("div");
    el.style.cssText = "position:fixed;inset:0;z-index:90;background:rgba(4,8,12,.72);display:flex;align-items:center;justify-content:center;cursor:pointer";
    el.innerHTML = '<div id="d3-title" style="position:absolute;z-index:2;top:9%;left:0;right:0;text-align:center;color:#f4dc9b;font:800 clamp(18px,3.2vw,34px) Cinzel,Georgia,serif;letter-spacing:.08em">Who goes first?</div>' +
      '<div id="d3-tags" style="position:absolute;z-index:2;inset:0;pointer-events:none"></div>' +
      '<div id="d3-win" style="position:absolute;z-index:2;bottom:9%;left:0;right:0;text-align:center;color:#fff;font:800 clamp(22px,4.4vw,48px) Cinzel,Georgia,serif;opacity:0;transition:opacity .6s"></div>' +
      '<div style="position:absolute;z-index:2;bottom:3%;left:0;right:0;text-align:center;color:#9fb0b0;font:13px system-ui" id="d3-src"></div>';
    document.body.appendChild(el);
    return el;
  }

  async function ceremony(ev) {
    const THREE = await three();
    const rounds = Object.entries(ev.rolls || {}).sort((a, b) => a[0] - b[0]).map(([, m]) => m);
    if (!rounds.length) return;
    const el = overlay(), canvas = document.createElement("canvas");
    canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;z-index:0";   // (a page may style every canvas)
    el.insertBefore(canvas, el.firstChild);
    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 60);
    const size = () => { renderer.setSize(innerWidth, innerHeight, false); camera.aspect = innerWidth / innerHeight; camera.position.set(0, innerWidth < innerHeight ? 15 : 11.5, innerWidth < innerHeight ? 8 : 6.8); camera.lookAt(0, 0, 0.6); camera.updateProjectionMatrix(); };
    size(); addEventListener("resize", size);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x223344, 1.1));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6); sun.position.set(3, 8, 4); scene.add(sun);
    const felt = new THREE.Mesh(new THREE.CircleGeometry(5.4, 64), new THREE.MeshStandardMaterial({ color: 0x0e3b2a, roughness: 0.95 }));
    felt.rotation.x = -Math.PI / 2; scene.add(felt);
    const rim = new THREE.Mesh(new THREE.TorusGeometry(5.4, 0.18, 12, 80), new THREE.MeshStandardMaterial({ color: 0x6b4a22, roughness: 0.6 }));
    rim.rotation.x = Math.PI / 2; scene.add(rim);

    const names = [...new Set(rounds.flatMap(r => Object.keys(r)))];
    const colorOf = n => COL[n] ?? FALLBACK[names.indexOf(n) % FALLBACK.length];
    const tags = el.querySelector("#d3-tags");
    const tagFor = (n, v, die) => {
      const d = document.createElement("div");
      d.style.cssText = "position:absolute;transform:translate(-50%,-50%);text-align:center;color:#fff;font:800 clamp(14px,2.2vw,26px) system-ui;text-shadow:0 2px 8px #000;opacity:0;transition:opacity .4s";
      d.innerHTML = `<div style="font-size:.6em;color:#cfdcdc;font-weight:600">${n.replace(/[<>&]/g, "")}</div><div style="font-size:1.9em;color:#${colorOf(n).toString(16).padStart(6, "0")}">${v}</div>`;
      tags.appendChild(d); d.userData = { die }; return d;
    };
    const live = [];                                     // dice in the air or settled, with their tag
    const throwRound = (rollMap, label) => new Promise(res => {
      el.querySelector("#d3-title").textContent = label;
      const who = Object.keys(rollMap), n = who.length, dice = [];
      who.forEach((name, i) => {
        const g = new THREE.Mesh(new THREE.IcosahedronGeometry(0.62, 0),
          new THREE.MeshStandardMaterial({ color: colorOf(name), roughness: 0.35, metalness: 0.15, flatShading: true }));
        const ang = (i - (n - 1) / 2) * 0.95;
        const target = new THREE.Vector3(Math.sin(ang) * 2.5, 0.62, 0.4 + Math.cos(ang) * 0.6 + (i % 2 ? 0.8 : -0.4));
        g.position.set(target.x * 0.4 + (Math.random() - 0.5) * 2, 6 + Math.random(), 7.5);
        const flight = 0.95 + Math.random() * 0.25;                       // seconds until the first touchdown
        g.userData = { name, v: rollMap[name], target, vel: new THREE.Vector3((target.x - g.position.x) / flight, 0, (target.z - g.position.z) / flight),
                       spin: new THREE.Vector3(Math.random() * 14 - 7, Math.random() * 14 - 7, Math.random() * 14 - 7), t: 0, bounces: 0, done: false };
        g.userData.vel.y = (0.62 - g.position.y + 0.5 * 18 * flight * flight) / flight;
        scene.add(g); dice.push(g);
      });
      live.push(...dice);
      const t0 = performance.now(); let last = t0;
      const step = now => {
        const dt = Math.min(0.04, (now - last) / 1000); last = now;
        let allDone = true;
        for (const d of dice) {
          const u = d.userData;
          if (!u.done) {
            u.vel.y -= 18 * dt; d.position.addScaledVector(u.vel, dt);
            d.rotation.x += u.spin.x * dt; d.rotation.y += u.spin.y * dt; d.rotation.z += u.spin.z * dt;
            if (d.position.y <= 0.62 && u.vel.y < 0) {
              d.position.y = 0.62; u.bounces++;
              u.vel.y *= -0.42; u.vel.x *= 0.62; u.vel.z *= 0.62; u.spin.multiplyScalar(0.55);
              if (u.bounces >= 3 || Math.abs(u.vel.y) < 1.2) { u.done = true; u.vel.set(0, 0, 0); u.tag = tagFor(u.name, u.v, d); }
            }
          }
          allDone = allDone && u.done;
        }
        for (const d of live) if (d.userData.tag) {                    // keep each number over its die
          const p = d.position.clone(); p.y += 1.35; p.project(camera);
          d.userData.tag.style.left = (p.x * 0.5 + 0.5) * innerWidth + "px"; d.userData.tag.style.top = (-p.y * 0.5 + 0.5) * innerHeight + "px";
          d.userData.tag.style.opacity = 1;
        }
        renderer.render(scene, camera);
        if (!allDone) requestAnimationFrame(step); else setTimeout(res, 1500);
      };
      requestAnimationFrame(step);
    });
    const finish = () => { removeEventListener("resize", size); renderer.dispose(); el.remove(); };
    el.addEventListener("click", () => { finish(); done(); });
    let closed = false; const done = () => { if (!closed) { closed = true; busy = false; next(); } };
    for (let i = 0; i < rounds.length && !closed; i++) {
      if (i > 0) { for (const d of live) { scene.remove(d); if (d.userData.tag) d.userData.tag.remove(); } live.length = 0; }
      await throwRound(rounds[i], i === 0 ? "Who goes first?" : "A tie: roll again");
    }
    if (closed) return;
    const win = el.querySelector("#d3-win");
    win.textContent = ev.winner ? `${ev.winner} goes first` : ""; win.style.opacity = 1;
    el.querySelector("#d3-src").textContent = ev.source ? `d${ev.sides || 20} · ${ev.source}` : "";
    setTimeout(() => { if (!closed) { finish(); done(); } }, 6000);
  }

  function next() { if (busy || !queue.length) return; busy = true; ceremony(queue.shift()).catch(() => { busy = false; }); }

  async function poll() {
    try {
      const r = await (await fetch("/api/events?since=" + (cursor ?? "latest"))).json();
      if (cursor !== null && !r.restarted) for (const e of r.events || []) {
        if (e.type === "highroll" && e.winner && e.rolls) { queue.push(e); next(); }
      }
      cursor = r.last;
    } catch {}
    setTimeout(poll, 1500);
  }
  addEventListener("load", () => setTimeout(poll, 600));
})();
