"""A public board view for the table: every player's battlefield, command zone, graveyard and life,
with each card's real Oracle text from the offline card file. Nothing from anyone's hand.

The board itself is kept by hand (by the brain, from what the table announces and from photos) in
table/.cache/boardview/board.json; life comes live from the table server. This page re-renders
every few seconds, so editing the JSON updates the screen.

  ~/.venvs/table/bin/python table/boardview.py        # http://localhost:8803
"""
from __future__ import annotations

import html
import json
import re
import sys
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import oracle  # noqa: E402

DIR = HERE / ".cache" / "boardview"
DATA = DIR / "board.json"
PORT = 8803

# Card frame colour from the mana cost (or a land's text): the one cue a glance needs.
COLOR = {"W": "#e9e3c9", "U": "#9fc3e6", "B": "#9b8fa3", "R": "#e3a089", "G": "#9fc79a"}


def colors_of(card: dict | None, name: str) -> list[str]:
    if not card:
        return []
    src = card.get("cost") or ""
    if "Land" in (card.get("type") or ""):
        src = " ".join(re.findall(r"\{[WUBRG]\}", card.get("text") or ""))
    return [c for c in "WUBRG" if "{" + c in src or "{" + c + "/" in src]


def esc(s) -> str:
    return html.escape(str(s or ""))


def mana(cost: str) -> str:
    return "".join(f'<i class="pip p{esc(m.strip("{}").replace("/", ""))}">{esc(m.strip("{}"))}</i>'
                   for m in re.findall(r"\{[^}]+\}", cost or ""))


def card_html(entry, small=False) -> str:
    if isinstance(entry, str):
        entry = {"name": entry}
    name = entry["name"]
    c = None if entry.get("token") or name.startswith("…") else oracle.card(name)
    cols = colors_of(c, name)
    bg = (f"linear-gradient(135deg,{','.join(COLOR[x] for x in cols)})" if len(cols) > 1
          else COLOR[cols[0]] if cols else "#d6d6d6")
    pt = entry.get("pt") or (f"{c['power']}/{c['toughness']}" if c and c.get("power") is not None else "")
    text = esc((c or {}).get("text", "")).replace("\n", "<br>")
    note = f'<div class="note">{esc(entry["note"])}</div>' if entry.get("note") else ""
    if small:
        return f'<span class="chip" style="background:{bg}" title="{text.replace("<br>", " ")}">{esc(name)}</span>'
    return (f'<article class="card" style="--frame:{bg}"><header><b>{esc(name)}</b>'
            f'<span>{mana((c or {}).get("cost", ""))}</span></header>'
            f'<div class="type">{esc((c or {}).get("type", "Token" if entry.get("token") else ""))}</div>'
            f'<div class="text">{text}</div>{note}'
            + (f'<div class="pt">{esc(pt)}</div>' if pt else "") + "</article>")


def life_now() -> dict:
    try:
        return json.loads(urllib.request.urlopen("http://127.0.0.1:8800/api/life", timeout=2).read())
    except Exception:
        return {}


def render() -> str:
    try:
        board = json.loads(DATA.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return f"<p>board.json unreadable: {esc(e)}</p>"
    life = life_now()
    cols = []
    for p in board["players"]:
        lv = life.get(p["name"], p.get("life"))
        cmd = (f'<section><h3>Command zone</h3>{card_html({"name": p["commander"], "note": p.get("commander_note")})}</section>'
               if p.get("commander_zone") else "")
        cols.append(
            f'<div class="player"><h2>{esc(p["name"])} <span class="life">{esc(lv)}</span></h2>'
            f'<div class="cmdr">{esc(p.get("commander"))}</div>'
            f'<section><h3>Creatures</h3>{"".join(card_html(x) for x in p.get("creatures", [])) or "<p class=empty>none</p>"}</section>'
            f'<section><h3>Other permanents</h3>{"".join(card_html(x) for x in p.get("other", [])) or "<p class=empty>none</p>"}</section>'
            f'<section><h3>Lands ({len(p.get("lands", []))})</h3><div class="chips">{"".join(card_html(x, True) for x in p.get("lands", []))}</div></section>'
            f'{cmd}'
            f'<section><h3>Graveyard</h3><div class="chips">{"".join(card_html(x, True) for x in p.get("graveyard", [])) or "<span class=empty>empty</span>"}</div></section>'
            "</div>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5">
<title>Board State</title><style>
:root{{--bg:#14161a;--panel:#1d2026;--ink:#ecebe6;--dim:#9aa0a8;--line:#2c3038}}
@media (prefers-color-scheme: light){{:root{{--bg:#f3f1ea;--panel:#fffdf7;--ink:#1d1d1b;--dim:#6b6b66;--line:#ddd8cc}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,sans-serif;padding:16px}}
h1{{font-size:18px;margin:0 0 4px}} .sub{{color:var(--dim);font-size:13px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}}
.player{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px}}
h2{{margin:0;font-size:20px;display:flex;justify-content:space-between;align-items:center}}
.life{{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}} .cmdr{{color:var(--dim);font-size:13px;margin-bottom:6px}}
h3{{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin:12px 0 6px}}
.card{{background:var(--frame);color:#1b1b1b;border-radius:9px;padding:8px 10px;margin-bottom:8px;position:relative;border:1px solid #0003}}
.card header{{display:flex;justify-content:space-between;gap:6px}} .type{{font-size:12px;font-style:italic;margin:2px 0 4px}}
.text{{font-size:12.5px;line-height:1.35}} .note{{font-size:12px;margin-top:5px;padding-top:4px;border-top:1px dashed #0004;font-weight:600}}
.pt{{position:absolute;right:8px;bottom:6px;background:#1b1b1b;color:#fff;border-radius:6px;padding:1px 7px;font-weight:700}}
.card:has(.pt){{padding-bottom:26px}}
.pip{{display:inline-block;min-width:17px;height:17px;border-radius:50%;background:#ccc;color:#111;font:700 11px/17px system-ui;text-align:center;font-style:normal;margin-left:2px;border:1px solid #0005}}
.pW{{background:#f8f3d8}} .pU{{background:#8ec2ef}} .pB{{background:#8d8193;color:#fff}} .pR{{background:#ef9a7e}} .pG{{background:#8fcf8a}}
.chips{{display:flex;flex-wrap:wrap;gap:5px}} .chip{{color:#1b1b1b;border-radius:6px;padding:3px 7px;font-size:12.5px;border:1px solid #0003}}
.empty{{color:var(--dim);font-size:13px;margin:0}}
</style></head><body><h1>Board state</h1>
<div class="sub">Public information only — battlefield, command zone, graveyard, life. Card text from the offline Oracle file; life live from the table. Refreshes every 5 s.</div>
<div class="grid">{"".join(cols)}</div></body></html>"""


def loop():
    while True:
        try:
            (DIR / "index.html").write_text(render())
        except Exception as e:                     # never let a bad edit kill the page
            print(f"render failed: {type(e).__name__}: {e}", flush=True)
        time.sleep(3)


if __name__ == "__main__":
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "index.html").write_text(render())
    threading.Thread(target=loop, daemon=True).start()

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(DIR), **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.split("?")[0] in ("/board.json",):    # data file is not served; the page is
                return self.send_error(404)
            return super().do_GET()

    print(f"board view: http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
