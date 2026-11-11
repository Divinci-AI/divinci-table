"""The end-of-game wrap-up (2026-11-09): when a seat reaches 0 the `game-over` event carries the winner and the final life totals and a summary
built from the public log only (turns, life over time, damage, deaths, spells, the finishing blow); /api/game/summary serves the same to every
page; the pages load the banner; the final log is saved for the retro; a rematch waits for the host. A throwaway table on a free port (never a
live game's), TABLE_NO_VOICE=1: so no spoken line.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/game_over_test.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import impulse_test as I  # noqa: E402

R, check, M, C = I.R, I.R.check, "Michael", "Claude"


def page(t, path):
    with urllib.request.urlopen(t.b + path, timeout=10) as r:
        return r.read().decode()


def table_game_over():
    print("Table: a game ends")
    t = R.Table()
    try:
        t.start()
        c, d = t.call("GET", "/api/game/summary", host=False)
        check(c == 200 and d == {"over": False}, "before the end: nothing to show", d)
        t.turn(C)
        ss = t.onto(C, "Starfleet Security")
        t.lands(C, 1, "Mountain")
        t.hand(C, "Plasma Cascade")
        t.lands(C, 2, "Mountain")
        x = t.onto(M, "Xindi Surveyors")
        c, d = t.act(C, "cast", name="Plasma Cascade", targets=[{"at": M, "ref": f"#{x}"}])
        check(c == 200, "Claude casts Plasma Cascade at Michael's Xindi Surveyors", (c, d))
        t.decline(C)
        c, d = t.act(C, "attack", assign={f"#{ss}": M})
        t.act(M, "block", attacker=f"#{ss}")
        t.act(C, "damage")
        t.turn(M)
        hidden_hand = {h["name"] for h in t.state(M).get("hand", [])}
        top = t.call("POST", "/api/brain/peek", {"seat": M, "n": 12})[1].get("private", {}).get("top") or []
        ev0 = I.last_event(t)
        c, d = t.call("POST", "/api/brain/life", {"seat": C, "player": M, "delta": -30, "text": "Phaser barrage."})
        check(c == 200, "Michael is brought to 0 or less", (c, d))
        time.sleep(0.5)
        ev = [e for e in t.events() if e["id"] > ev0 and e["type"] == "game-over"]
        check(len(ev) == 1 and ev[0].get("winner") == C and ev[0].get("totals", {}).get(M, 1) <= 0 and ev[0].get("totals", {}).get(C) == 20,
              "one game-over event, with the winner and the final totals", ev)
        e = ev[0] if ev else {}
        s = e.get("summary") or {}
        check(s.get("turns", 0) >= 2 and set(s.get("life", {})) == {M, C} and s["life"][M][0]["life"] == 20 and s["life"][M][-1]["life"] <= 0,
              "the summary: turns, each life total over time (from 20 to the end)", (s.get("turns"), s.get("life")))
        check(any(x["name"] == "Xindi Surveyors" for x in s.get("died", [])), "…the creature that died", s.get("died"))
        check(any(x["name"] == "Plasma Cascade" and x["player"] == C for x in s.get("key_spells", [])), "…a key spell, and who cast it", s.get("key_spells"))
        check((s.get("damage_dealt_on_their_turn") or {}).get(C, 0) >= 1 and (s.get("life_lost") or {}).get(M, 0) >= 30, "…damage dealt and life lost", (s.get("damage_dealt_on_their_turn"), s.get("life_lost")))
        fb = s.get("finishing_blow") or {}
        check(fb.get("player") == M and fb.get("amount") == 30, "…and the finishing blow", fb)
        c, g = t.call("GET", "/api/game/summary", host=False)
        check(c == 200 and g.get("over") and g.get("winner") == C and g.get("summary") == s and "host" in g.get("rematch", ""), "every page can read the same, and a rematch is the host's call", g)
        public_names = set()
        for st in t.call("GET", "/api/board3d", host=False)[1]["seats"]:
            public_names |= {p.get("name") for p in st.get("permanents", []) if p.get("name")} | set(st.get("graveyard", []))
        log = json.dumps(t.events())
        secret = sorted(n for n in (hidden_hand | set(top)) if n not in public_names and n not in log
                        and n not in ("Plains", "Forest", "Mountain", "Swamp", "Island"))
        blob = json.dumps(g)
        leaked = [n for n in secret if n in blob]
        check(secret and not leaked, f"no card from a hand or a library is in the summary ({len(secret)} hidden names checked)", leaked)
        check(not any(x.get("type") == "say" and x.get("action") == "game-over" for x in t.events()), "TABLE_NO_VOICE=1: no spoken line")
        final = list(t.dir.rglob("game-final.json"))
        fj = json.loads(final[0].read_text()) if final else {}
        check(final and fj.get("winner") == C and fj.get("events") and fj.get("summary") == s, "the final log is saved for the retro (game-final.json)", [str(p) for p in final])
        check(t.legal(C).get("note") == "the game is over", "nothing restarts: the legal list says the game is over", t.legal(C).get("note"))
        for path in ("/hand", "/me", "/board", "/table", "/3d"):
            try:
                ok = "/assets/gameover.js" in page(t, path)
            except Exception as ex:  # noqa: BLE001
                ok = str(ex)
            check(ok is True, f"{path} loads the game-over banner", ok)
        c, d = t.call("POST", "/api/game-over", {"order": [C, M], "winner": C})
        check(c == 409, "a second game-over is refused", (c, d))
        c, d = t.call("POST", "/api/reset", {})
        time.sleep(0.5)
        g2 = t.call("GET", "/api/game/summary", host=False)[1]
        check(g2 == {"over": False}, "the host's reset (the rematch) clears it", g2)
    finally:
        t.stop()


def banner_escapes():
    print("assets/gameover.js renders the summary escaped")
    if not shutil.which("node"):
        print("  ⏭️  skipped: this check runs the page's script in Node, and node is not installed")
        return
    js = (HERE / "assets" / "gameover.js").read_text()
    harness = r"""
const vm = require("vm"); const html = []; const els = [];
function el() { return { style: {}, setAttribute() {}, set innerHTML(v) { html.push(v); this._h = v; }, get innerHTML() { return this._h; }, set textContent(v) { this._t = v; }, remove() {} }; }
const document = { readyState: "complete", head: { appendChild() {} }, body: { appendChild(e) { els.push(e); } }, createElement: el,
  querySelector: q => els.find(e => (q === ".go-banner" ? e.className === "go-banner" : e.className === "go-pill")) || null, querySelectorAll: () => [], addEventListener() {} };
const evil = '<img src=x onerror=alert(1)>';
const d = { over: true, winner: evil, order: [evil, "B"], totals: { [evil]: 3, B: 0 }, at: 1, rematch: evil,
  summary: { turns: 4, life: { [evil]: [{ turn: 0, life: 20 }] }, died: [{ name: evil }], key_spells: [{ name: evil, player: evil }], damage_dealt_on_their_turn: { [evil]: 5 },
             finishing_blow: { player: "B", amount: 3, by: evil, on_turn_of: evil, line: evil } } };
const ctx = { document, window: {}, fetch: async () => ({ json: async () => d }), setTimeout: () => 0, console };
vm.createContext(ctx); vm.runInContext(process.argv[process.argv.length - 1], ctx);
setTimeout(() => { const out = html.join("\n"); console.log(JSON.stringify({ raw: out.includes("<img"), shown: out.includes("&lt;img"), n: html.length, err: ctx.window.__gameOverError || null })); }, 50);
"""
    r = subprocess.run(["node", "-e", harness, js], capture_output=True, text=True, timeout=30)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        res = {"error": r.stdout[-300:] + r.stderr[-300:]}
    check(res.get("shown") and res.get("raw") is False, "a card or player name with HTML in it is shown as text, never run", res)


def main():
    for fn in (banner_escapes, table_game_over):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {fn.__name__}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
