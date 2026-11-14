"""Live web draft: two people (Michael, Sam) plus six bots, the same packs and pass order as draft.py.

    python3 draft_server.py [--seed 7] [--port 8820]

Binds 127.0.0.1 only. Each person opens their own link (a secret in ?k=); tokens are written to
../live/draft-links.txt (0600). Remote players come in through a tunnel. Card art is served only to a valid seat key, and only for the cards
that person has been shown (their current pack and pool). Each person sees only their own pack.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import deckbuild
import draft as D
import draft_banter

HOME = D.ROOT
IMAGES = HOME / "images" / "normal"
LIVE = Path(os.environ.get("DRAFT_LIVE") or HOME / "live")   # tests point this at a scratch folder
PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP", "CF-Connecting-IP", "True-Client-IP")
HUMANS = {i: n.strip() for i, n in enumerate(os.environ.get("DRAFT_HUMANS", "Michael,Sam").split(",")) if n.strip()}   # the people drafting: DRAFT_HUMANS="Ann,Ben"
AGENTS = {2: "Claude", 3: "Fusion"}          # AI participants (they pick for their own seat via the same API); the rest are bots
NAMES = {**HUMANS, **AGENTS}
REQUIRED = set(NAMES)                        # a step advances when all four have picked
# Match handoff: the host publishes each player's table link here; a link is only ever shown to its own player. A published link must
# point at the host's own table (loopback) or at a Cloudflare quick tunnel; DRAFT_LINK_HOST_SUFFIXES adds more (comma-separated).
LINK_HOSTS = ("127.0.0.1", "localhost")
LINK_SUFFIXES = (".trycloudflare.com",) + tuple(x.strip().lower() for x in os.environ.get("DRAFT_LINK_HOST_SUFFIXES", "").split(",") if x.strip())
LOOPBACK = ("127.0.0.1", "::1")


def clean_link(url) -> str | None:
    """A table link the host may publish, or None: http(s) only, no credentials/whitespace/backslashes, and the host must be loopback
    (plain http allowed there) or a trusted https tunnel suffix. The #key=... fragment is part of the link and is kept."""
    if not isinstance(url, str) or not 8 <= len(url) <= 2000 or "\\" in url or any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        return None
    try:
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower()
        u.port                                          # raises on a bad port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not host or u.username is not None or u.password is not None:
        return None
    if host in LINK_HOSTS:
        return url
    return url if u.scheme == "https" and any(host.endswith(s) for s in LINK_SUFFIXES) else None


def private_write(path: Path, text: str) -> None:
    """Write a 0600 file atomically (a reader never sees a half-written one, and it is never world-readable)."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def make_bots(names) -> list[str]:
    """`--bot Sam`: that person or agent isn't playing tonight, so a bot drafts their seat. The draft no longer waits on it and
    it gets no link or token. Call before creating the Draft. Returns the names it changed (unknown names are ignored)."""
    done = []
    for n in names or []:
        seat = next((s for s, nm in NAMES.items() if nm.lower() == str(n).lower()), None)
        if seat is None:
            continue
        HUMANS.pop(seat, None); AGENTS.pop(seat, None); NAMES.pop(seat, None); REQUIRED.discard(seat)
        done.append(n)
    return done


class Draft:
    def __init__(self, seed: int, keep: Path | None = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.cards = {c["id"]: c for c in json.loads(D.CARDS_JSON.read_text())}
        pool = D.load_pool()
        self.packs = [[D.make_pack(pool, self.rng) for _ in range(D.SEATS)] for _ in range(D.PACKS)]
        self.picks: list[list[dict]] = [[] for _ in range(D.SEATS)]
        self.rnd, self.pick_no = 0, 0
        self.current = self.packs[0]
        self.pending: dict[int, str] = {}          # seat -> card id picked this round of picks
        self.done = False
        self.history: list[dict] = []              # each finished step: {seat: card id}; replayed after a restart
        self.quiet = False                         # True while a restart replays history: no banter for old steps
        self.banter = draft_banter.Banter([c["name"] for c in self.cards.values() if c.get("set") == "trk"],
                                          LIVE / "draft-banter.jsonl", NAMES, set(HUMANS.values()))
        self.chat_at: dict[int, float] = {}
        self.voice = draft_banter.HostVoice(enabled=True)
        self.banter.on_line = lambda e: self.voice.speak(e["who"], e["text"]) if not self.quiet else None
        self.decks: dict[int, dict] | None = None  # after the draft: seat -> {"main": {card id: n}, "basics": {land: n}}
        self.ready: set[int] = set()               # seats that pressed "I'm ready" on a legal deck (saved with the decks)
        self.match: dict | None = self._load_match()   # {"links": {player name: url}, "note": str}: the host's handoff, never logged
        self.tokens = {secrets.token_urlsafe(16): s for s in NAMES}
        if keep:                                       # a restart keeps the same links
            old = {l.split(": ")[0]: l.strip().split("k=")[1] for l in keep.read_text().splitlines() if "k=" in l}
            fresh = {s: t for t, s in self.tokens.items()}
            self.tokens = {old.get(n) or fresh[s]: s for s, n in NAMES.items()}

    def view(self, seat: int) -> dict:
        d = {"you": NAMES[seat], "round": self.rnd + 1, "pick": self.pick_no + 1, "done": self.done,
             "waiting_on": [NAMES[s] for s in NAMES if s not in self.pending and not self.done],
             "picked_this_turn": seat in self.pending, "my_pick": self.pending.get(seat),
             "pool": [self._card(c) for c in self.picks[seat]], "seat": seat}
        if not self.done:
            d["pack"] = [self._card(c) for c in self.current[seat]]
        else:
            d["deck"] = self.deck_view(seat)
            d["ready"] = [{"name": n, "kind": "human" if s in HUMANS else "agent", "ready": s in self.ready} for s, n in NAMES.items()]
        return d

    # ---- deck editing (after the draft)
    BASICS = ("Plains", "Island", "Swamp", "Mountain", "Forest")

    def _suggest(self, seat: int) -> dict:
        b = deckbuild.build(self.picks[seat])
        main: dict[str, int] = {}
        for i in b["spells"]:
            main[i] = main.get(i, 0) + 1
        return {"main": main, "basics": {k: int(v) for k, v in b["basics"].items()}}

    def _decks_init(self) -> None:
        if self.decks is not None:
            return
        self.decks = {}
        f = LIVE / "draft-decks.json"
        saved = json.loads(f.read_text()) if f.exists() else {}
        if saved.get("seed") != self.seed:          # a file left by an earlier draft (or the old, untagged format): ignore it
            saved = {}
        decks = saved.get("decks") or {}
        kept = set()
        for seat in NAMES:
            dk = decks.get(str(seat))
            if dk and self._owns(seat, dk):
                self.decks[seat] = dk
                kept.add(seat)
            else:
                self.decks[seat] = self._suggest(seat)
        for s in saved.get("ready") or []:           # an older file has no "ready": nobody is ready
            try:
                seat = int(s)
            except (TypeError, ValueError):
                continue
            if seat in kept and sum(self._count(seat, self.decks[seat])) == deckbuild.DECK_SIZE:
                self.ready.add(seat)
        self._write_decks()

    def _owns(self, seat: int, dk: dict) -> bool:
        """A saved deck is usable only if every card in it is one this seat drafted, in the copies drafted."""
        owned: dict[str, int] = {}
        for c in self.picks[seat]:
            owned[c["id"]] = owned.get(c["id"], 0) + 1
        return all(0 <= int(n) <= owned.get(i, 0) for i, n in (dk.get("main") or {}).items())

    def _write_decks(self) -> None:
        f = LIVE / "draft-decks.json"
        f.touch(mode=0o600)
        f.write_text(json.dumps({"seed": self.seed, "decks": {str(k): v for k, v in (self.decks or {}).items()},
                                 "ready": sorted(self.ready)}))
        for seat, name in NAMES.items():
            dk = self.decks[seat]
            lines = [f"{n} {self.cards[i]['name']}" for i, n in dk["main"].items() if n > 0]
            lines += [f"{n} {b}" for b, n in dk["basics"].items() if n > 0]
            (LIVE / f"deck-{name.lower()}.txt").write_text("\n".join(lines) + "\n")

    def _count(self, seat: int, dk: dict) -> tuple[int, int]:
        spells = lands = 0
        for i, n in dk["main"].items():
            if "Land" in self.cards[i]["type_line"]:
                lands += n
            else:
                spells += n
        return spells, lands + sum(dk["basics"].values())

    def deck_view(self, seat: int) -> dict:
        self._decks_init()
        dk = self.decks[seat]
        sp, ln = self._count(seat, dk)
        total = sp + ln
        return {"main": dk["main"], "basics": dk["basics"], "spells": sp, "lands": ln, "total": total,
                "ready": total == deckbuild.DECK_SIZE, "ready_flag": seat in self.ready}

    def set_deck(self, seat: int, body: dict) -> tuple[int, dict]:
        with self.lock:
            if not self.done:
                return 400, {"error": "the draft is not over yet"}
            self._decks_init()
            owned: dict[str, int] = {}
            for c in self.picks[seat]:
                owned[c["id"]] = owned.get(c["id"], 0) + 1
            main, basics = {}, {}
            try:
                for i, n in (body.get("main") or {}).items():
                    n = int(n)
                    if n < 0 or n > owned.get(i, 0):
                        return 400, {"error": "you can only use the copies you drafted"}
                    if n:
                        main[i] = n
                for b, n in (body.get("basics") or {}).items():
                    n = int(n)
                    if b not in self.BASICS or not 0 <= n <= 60:
                        return 400, {"error": "basics are Plains, Island, Swamp, Mountain, Forest (0 to 60)"}
                    if n:
                        basics[b] = n
            except (TypeError, ValueError, AttributeError):
                return 400, {"error": "bad deck"}
            was = self.deck_view(seat)["ready"]
            if self.decks[seat] != {"main": main, "basics": basics}:
                self.ready.discard(seat)               # an edit after "I'm ready" cancels it (a re-save of the same deck does not)
            self.decks[seat] = {"main": main, "basics": basics}
            self._write_decks()
            v = self.deck_view(seat)
            if v["ready"] and not was:
                self.banter.event("deck-ready", f"{NAMES[seat]}'s deck now has exactly 40 cards")
            return 200, v

    def reset_deck(self, seat: int) -> tuple[int, dict]:
        with self.lock:
            if not self.done:
                return 400, {"error": "the draft is not over yet"}
            self._decks_init()
            fresh = self._suggest(seat)
            if self.decks[seat] != fresh:
                self.ready.discard(seat)
            self.decks[seat] = fresh
            self._write_decks()
            return 200, self.deck_view(seat)

    # ---- ready flags and the match handoff
    def set_ready(self, seat: int, flag: bool) -> tuple[int, dict]:
        with self.lock:
            if not self.done:
                return 400, {"error": "the draft is not over yet"}
            self._decks_init()
            if flag:
                total = sum(self._count(seat, self.decks[seat]))
                if total != deckbuild.DECK_SIZE:
                    return 400, {"error": f"a deck needs exactly {deckbuild.DECK_SIZE} cards (yours has {total})"}
                self.ready.add(seat)
            else:
                self.ready.discard(seat)
            self._write_decks()
            return 200, {"ok": True, "ready": seat in self.ready}

    def host_ready_state(self) -> dict:
        """For the host's launcher (loopback only): who is ready. Names and flags only: no tokens, no links."""
        with self.lock:
            if self.done:
                self._decks_init()
            players = {n: {"kind": "human" if s in HUMANS else "agent", "ready": self.done and s in self.ready,
                           "total": sum(self._count(s, self.decks[s])) if self.done else None} for s, n in NAMES.items()}
            return {"draft_done": self.done, "players": players,
                    "all_ready": self.done and all(s in self.ready for s in NAMES)}

    def _load_match(self) -> dict | None:
        try:
            data = json.loads((LIVE / "draft-match.json").read_text())
            if data.get("seed") != self.seed:
                return None
            links = {n: u for n, u in (data.get("links") or {}).items() if n in NAMES.values() and clean_link(u)}
            return {"links": links, "note": str(data.get("note") or "")[:200]} if links else None
        except (OSError, ValueError, AttributeError):
            return None

    def publish_match(self, body: dict) -> tuple[int, dict]:
        """The host's handoff: {"links": {player: url}, "note": str} replaces what was published; {"clear": true} withdraws it.
        Replies carry names only, never a link."""
        with self.lock:
            if not isinstance(body, dict):
                return 400, {"error": "bad request"}
            if body.get("clear"):
                self.match = None
            else:
                if not self.done:
                    return 409, {"error": "the draft is not over yet"}
                raw = body.get("links")
                if not isinstance(raw, dict) or not raw:
                    return 400, {"error": "links must be {player: url}"}
                by_lower = {n.lower(): n for n in NAMES.values()}
                links = {}
                for who, url in raw.items():
                    name = by_lower.get(str(who).lower())
                    if name is None:
                        return 400, {"error": "unknown player in links"}
                    if clean_link(url) is None:
                        return 400, {"error": f"the link for {name} is not an allowed table link"}
                    links[name] = url
                note = "".join(ch for ch in str(body.get("note") or "") if ch >= " " and ch != "\x7f")[:200]
                self.match = {"links": links, "note": note}
            f = LIVE / "draft-match.json"
            if self.match:
                private_write(f, json.dumps({"seed": self.seed, **self.match}))
            else:
                f.unlink(missing_ok=True)
            return 200, {"ok": True, "published": sorted(self.match["links"]) if self.match else []}

    def match_for(self, seat: int) -> dict:
        """What THIS seat may know about the match: its own link (never anyone else's) and where things stand."""
        with self.lock:
            link = (self.match or {}).get("links", {}).get(NAMES[seat])
            if link:
                status = "ready"
            else:
                if self.done:
                    self._decks_init()
                status = "starting" if self.done and all(s in self.ready for s in NAMES) else "waiting"
            return {"status": status, "link": link, "note": (self.match or {}).get("note", "")}

    @staticmethod
    def _card(c: dict) -> dict:
        pt = f"{c['power']}/{c['toughness']}" if c.get("power") is not None else ""
        return {"id": c["id"], "name": c["name"], "cost": c["mana_cost"] or "", "type": c["type_line"],
                "text": c["oracle_text"], "rarity": c["rarity"], "pt": pt, "colors": c["colors"], "cmc": c["cmc"]}

    def pick(self, seat: int, cid: str) -> str | None:
        with self.lock:
            if self.done:
                return "the draft is over"
            if cid not in {c["id"] for c in self.current[seat]}:
                return "that card is not in your pack"
            self.pending[seat] = cid
            if all(s in self.pending for s in REQUIRED):
                self._advance()
            self._persist()
            return None

    def _persist(self) -> None:
        st = LIVE / "draft-state.json"
        st.touch(mode=0o600)
        st.write_text(json.dumps({"seed": self.seed, "history": self.history,
                                  "pending": {str(k): v for k, v in self.pending.items()}}))

    def restore(self, path: Path) -> None:
        data = json.loads(path.read_text())
        self.quiet = True
        for step in data["history"]:
            for seat, cid in step.items():
                self.pending[int(seat)] = cid
            self._advance()
        self.quiet = False
        for seat, cid in data.get("pending", {}).items():
            self.pending[int(seat)] = cid

    def _advance(self) -> None:
        self.history.append({str(k): v for k, v in self.pending.items()})
        for seat in range(D.SEATS):
            pack = self.current[seat]
            if not pack:
                continue
            if seat in self.pending:
                idx = next(i for i, c in enumerate(pack) if c["id"] == self.pending[seat])
            else:
                idx = D.bot_pick(pack, self.picks[seat], self.rng)
            self.picks[seat].append(pack.pop(idx))
        self.pending = {}
        direction = 1 if self.rnd % 2 == 0 else -1
        self.current = [self.current[(s - direction) % D.SEATS] for s in range(D.SEATS)]
        self.pick_no += 1
        if self.pick_no >= D.PACK_SIZE:
            self.rnd += 1
            self.pick_no = 0
            if self.rnd >= D.PACKS:
                self.done = True
                self._save()
                if not self.quiet:
                    self.banter.event("draft-done")
                return
            self.current = self.packs[self.rnd]
            if not self.quiet:
                self.banter.event("pack-open", f"pack {self.rnd + 1} of {D.PACKS}")
        elif not self.quiet:
            self.banter.event("pass", f"pick {self.pick_no + 1} of {D.PACK_SIZE}")

    def _save(self) -> None:
        LIVE.mkdir(exist_ok=True)
        D.OUT_DIR.mkdir(exist_ok=True)
        (D.OUT_DIR / f"draft-live-seed{self.seed}.json").write_text(json.dumps(
            {"seed": self.seed, "humans": HUMANS, "agents": AGENTS, "all_pools": [[c["id"] for c in p] for p in self.picks]}, indent=1))


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Star Trek draft</title>
<style>
body{margin:0;background:#10151a;color:#e8ecf1;font-family:system-ui,sans-serif}
header{padding:12px 16px;border-bottom:1px solid #223;display:flex;gap:14px;flex-wrap:wrap;align-items:baseline}
header b{font-size:18px} .st{color:#8fa} .wait{color:#e9b44c}
main{display:flex;gap:16px;padding:12px 16px;flex-wrap:wrap}
#pack{flex:3 1 560px;display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;align-content:start}
#side{flex:1 1 260px;max-width:380px}
.c{background:#18202a;border:2px solid #223;border-radius:10px;padding:8px;cursor:pointer}
.c:hover{border-color:#e9b44c} .c.sel{border-color:#6f6;background:#1c2a20}
.c img{width:100%;border-radius:6px;display:block;margin-bottom:6px}
.n{font-weight:700} .m{color:#9ab;font-size:12px} .t{font-size:12px;line-height:1.3;margin-top:4px;white-space:pre-line}
.r-rare,.r-mythic{border-left:5px solid #e9b44c} .r-uncommon{border-left:5px solid #9bd}
button{background:#2a6;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:16px;cursor:pointer}
button:disabled{background:#444;cursor:default}
pre{background:#18202a;border-radius:8px;padding:10px;font-size:12px;white-space:pre-wrap}
h3{margin:14px 0 6px;font-size:14px;color:#9ab;letter-spacing:.06em}
@media (max-width:700px){
 main{padding:8px 0;display:block}
 #pack{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;gap:12px;padding:0 6vw;-webkit-overflow-scrolling:touch}
 #pack .c{flex:0 0 88vw;scroll-snap-align:center;cursor:default}
 #pack .c.sel{border-color:#223;background:#18202a}
 #pack .c img{width:100%}
 #bar{position:sticky;bottom:0;background:#10151aee;padding:10px 16px;display:flex;gap:10px;align-items:center;justify-content:space-between;border-top:1px solid #223}
 #bar button{flex:1;padding:14px}
 #side{padding:0 16px;max-width:none}
}
#bar{margin-top:10px;display:flex;gap:10px;align-items:center} #cnt{color:#9ab;font-size:14px;min-width:54px}
.dots{display:inline-flex;gap:3px;vertical-align:middle;margin-right:6px}
.d{width:11px;height:11px;border-radius:50%;border:1px solid #0008;display:inline-block}
.d-W{background:#f4efc9}.d-U{background:#3a8fe0}.d-B{background:#5b4a63}.d-R{background:#e0523a}.d-G{background:#3f9d52}.d-C{background:#9aa3ab}
#sum{display:flex;gap:10px;flex-wrap:wrap;margin:4px 0 8px;font-size:14px}
#sum span{display:inline-flex;align-items:center;gap:4px}
.pr{padding:3px 4px;border-radius:5px;cursor:pointer;font-size:13px}.pr:hover{background:#223}
#pv{position:fixed;display:none;z-index:50;width:300px;max-width:80vw;background:#0c1116f5;border:1px solid #345;border-radius:12px;padding:8px;box-shadow:0 8px 30px #000a;pointer-events:none}
#pv img{width:100%;border-radius:8px;display:block}#pv .n{margin-top:6px}
@media (max-width:700px){#pv{left:50%!important;top:50%!important;transform:translate(-50%,-50%);width:86vw;pointer-events:auto}}
#pack .c.picked{border:3px solid #6f6;background:#1c2a20;box-shadow:0 0 0 2px #6f64;position:relative}
#pack .c.picked::after{content:"SELECTED";position:absolute;top:8px;right:8px;background:#2a6;color:#fff;font-weight:700;font-size:12px;letter-spacing:.08em;padding:3px 8px;border-radius:6px}
body.done #packcol,body.done #sum,body.done #pool,body.done #side>h3{display:none}
body.done main{display:block}body.done #side{max-width:none}
#edbar{position:sticky;top:0;z-index:5;background:#10151af2;padding:8px 0;display:flex;gap:10px;flex-wrap:wrap;align-items:center;border-bottom:1px solid #223}
.tot{font-weight:700;font-size:18px}.tot.ok{color:#6f6}.tot.bad{color:#e9b44c}
.sm{background:#2b3440;color:#e8ecf1;border:0;border-radius:8px;padding:8px 12px;font-size:14px;cursor:pointer}
.sm:hover{background:#38455a}
.cols{display:flex;gap:18px;flex-wrap:wrap;margin-top:8px}.cols>div{flex:1 1 300px;min-width:0}
.er{display:flex;align-items:center;gap:6px;padding:4px 4px;border-radius:6px;font-size:14px}
.er.in{background:#1c2a20}.er .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer}
.er .q{min-width:44px;text-align:center;color:#9ab;font-size:13px}
.pm{width:34px;height:34px;border-radius:8px;border:0;background:#2b3440;color:#fff;font-size:20px;cursor:pointer;padding:0}
.pm:disabled{opacity:.3;cursor:default}
.curve{display:flex;gap:6px;align-items:flex-end;height:64px;margin:8px 0}.curve div{flex:1;text-align:center;font-size:11px;color:#9ab}
.curve i{display:block;background:#4a7;border-radius:3px 3px 0 0;min-height:2px}
body{padding-bottom:84px}
#ttb{position:fixed;left:14px;bottom:16px;z-index:40;width:52px;height:52px;border-radius:50%;border:0;background:#2a6;color:#fff;font-size:24px;cursor:pointer;box-shadow:0 4px 14px #0008}
#ttb.new{animation:pulse 1s infinite}@keyframes pulse{50%{box-shadow:0 0 0 10px #2a64}}
#tt{display:none;position:fixed;left:14px;bottom:78px;z-index:40;width:360px;max-width:calc(100vw - 28px);background:#0c1116f5;border:1px solid #345;border-radius:12px;padding:10px;box-shadow:0 8px 30px #000a}
#tt.open{display:block}#ttl{height:220px;overflow-y:auto;font-size:14px;line-height:1.35}
.tl{padding:3px 0}.tl.ai{color:#dfe8ef}.tl b{color:#9ab;margin-right:4px}.tl b.cl{color:#e9b44c}.tl b.fu{color:#7ad}
#ttf{display:flex;gap:6px;margin-top:8px}#tti{flex:1;min-width:0;background:#18202a;border:1px solid #345;border-radius:8px;color:#e8ecf1;padding:8px;font-size:14px}
#tta{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
@media (max-width:700px){#ttb{bottom:92px}#tt{bottom:152px;left:14px;right:14px;width:auto}#ttl{height:30vh}}
.vis{display:flex;gap:22px;flex-wrap:wrap;align-items:flex-start;margin-top:8px}.vc{flex:1 1 320px;min-width:0}.vt{flex:1 1 300px;min-width:0}
.tr{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:13px}.tn{width:92px;color:#cfd8e0}
.tb{position:relative;flex:1;height:14px;background:#18202a;border-radius:4px;overflow:hidden}.tb i{display:block;height:100%;border-radius:4px}
.tb u{position:absolute;top:0;bottom:0;width:2px;background:#fff9;transform:translateX(-1px)}
.tc{width:70px;text-align:right}
#rdybar{display:none;position:fixed;left:0;right:0;bottom:0;z-index:30;background:#10151af7;border-top:1px solid #223;padding:10px 16px 10px 80px;gap:8px 14px;align-items:center;flex-wrap:wrap;justify-content:space-between}
body.done #rdybar{display:flex}body.done{padding-bottom:150px}
#rdys{font-size:13px;color:#9ab;flex:1 1 240px;min-width:0}#rdys .ok{color:#6f6;white-space:nowrap}#rdys .bld{color:#e9b44c;white-space:nowrap}
#rdyc{display:flex;gap:10px;align-items:center;flex-wrap:wrap}#rdymsg{font-size:14px;color:#cfd8e0}
#rdyb{font-size:17px;padding:12px 26px}
#rdyb.join{background:#e9b44c;color:#10151a;font-weight:800;font-size:20px;padding:14px 34px;animation:pulse 1s infinite}
@media (max-width:700px){#rdybar{padding:10px 16px}#rdyc{width:100%}#rdyb{flex:1}body.done #ttb{bottom:150px}body.done #tt{bottom:210px}}
</style></head><body>
<header><b>Star Trek draft</b><span id="who"></span><span id="prog" class="st"></span><span id="wait" class="wait"></span><span style="flex:1"></span><button class="sm" id="sfxb" title="sound effects">🔊</button></header>
<main><div id="packcol"><div id="pack"></div><div id="bar"><span id="cnt"></span><button id="go" disabled>Take this card</button></div></div>
<div id="side"><h3>YOUR POOL (<span id="np">0</span>)</h3><div id="sum"></div><div id="pool"></div><div id="ed"></div></div></main>
<div id="rdybar"><div id="rdys"></div><div id="rdyc"></div></div>
<div id="pv"></div>
<button id="ttb" title="table talk">💬</button>
<div id="tt"><div id="ttl"></div><div id="ttf"><input id="tti" maxlength="140" placeholder="say something to the table…" autocomplete="off"><button class="sm" id="tts">Send</button></div>
<div id="tta"><button class="sm" data-ask="Claude">Ask Claude</button><button class="sm" data-ask="Fusion">Ask Fusion</button><button class="sm" id="ttv">🔇 voices off</button><button class="sm" id="tth" style="display:none">🔊 host voice on</button></div></div>
<script>
const K=new URLSearchParams(location.search).get("k")||"";let sel=null,lastKey="",POOL={},POOLL=[],PICK=null,WAIT="",DONE=false,PREV=null,WASREADY=null;
function syncBtn(){const go=document.getElementById("go");
 if(DONE){go.disabled=true;go.textContent="Draft complete";return}
 if(!PICK){go.textContent="Take this card";go.disabled=!sel}
 else if(sel&&sel!==PICK){go.textContent="Switch to this card";go.disabled=false}
 else{go.textContent="Selected. Waiting on "+WAIT+"… (tap another card to switch)";go.disabled=true}}
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function card(c,art){return `<div class="c r-${c.rarity}${sel===c.id&&sel!==PICK?" sel":""}${PICK===c.id?" picked":""}" data-id="${c.id}">`+
 (art?`<img src="/art/${c.id}?k=${K}" loading="eager" onerror="this.remove()">`:"")+`<div class="n">${esc(c.name)} <span class="m">${esc(c.cost)}</span></div>`+
 `<div class="m">${esc(c.type)}${c.pt?" · "+c.pt:""} · ${c.rarity}</div><div class="t">${esc(c.text)}</div></div>`}
async function tick(){try{const d=await (await fetch("/api/state?k="+K,{cache:"no-store"})).json();
 if(d.error){document.body.innerHTML="<p style='padding:20px'>"+esc(d.error)+"</p>";return}
 document.getElementById("who").textContent="you: "+d.you;
 document.getElementById("prog").textContent=d.done?"draft complete":`pack ${d.round}, pick ${d.pick}`;
 document.getElementById("wait").textContent=d.done?"":(d.picked_this_turn?"picked; waiting on "+d.waiting_on.join(", "):"your pick");
 document.getElementById("np").textContent=d.pool.length;
 const key=d.round+"-"+d.pick+"-"+d.picked_this_turn+"-"+d.my_pick+"-"+d.done;
 if(PREV){if(d.done&&!PREV.done)SFX.play("fanfare");else if(d.round!==PREV.round&&!d.done)SFX.play("packopen");else if(d.pick!==PREV.pick&&!d.done)SFX.play("pass")}
 PREV={round:d.round,pick:d.pick,done:d.done};
 PICK=d.picked_this_turn?d.my_pick:null;WAIT=d.waiting_on.join(", ");DONE=d.done;
 if(key!==lastKey){lastKey=key;sel=null;document.getElementById("pack").innerHTML=(d.pack||[]).map(c=>card(c,d.art)).join("");
  if(PICK){const pc=document.querySelector("#pack .picked");if(pc)setTimeout(()=>pc.scrollIntoView({inline:"center",block:"nearest"}),30)}document.getElementById("pack").scrollLeft=0;setTimeout(follow,50)}
 syncBtn();
 POOL={};POOLL=d.pool;d.pool.forEach(c=>POOL[c.id]=c);
 const cnt={W:0,U:0,B:0,R:0,G:0,C:0};d.pool.forEach(c=>{if(/\bLand\b/.test(c.type))return;if(!c.colors.length)cnt.C++;c.colors.forEach(x=>cnt[x]++)});
 document.getElementById("sum").innerHTML=Object.entries(cnt).filter(([k,n])=>n>0||k!=="C").map(([k,n])=>`<span><i class="d d-${k}"></i>${n}</span>`).join("");
 document.getElementById("pool").innerHTML=groups().sort((a,b)=>(a.c.colors.length>1?"z":a.c.colors[0]||"y").localeCompare(b.c.colors.length>1?"z":b.c.colors[0]||"y")||a.c.name.localeCompare(b.c.name))
  .map(({c,owned})=>`<div class="pr" data-pid="${c.id}"><span class="dots">${(c.colors.length?c.colors:["C"]).map(x=>`<i class="d d-${x}"></i>`).join("")}</span>${esc(c.name)} <span class="m">${esc(c.cost)}</span>${owned>1?` <span class="m cnt">×${owned}</span>`:""}</div>`).join("");
 if(d.done&&d.deck){RDY=d.ready||[];MY_READY=!!d.deck.ready_flag;onDone(d);drawRdy();matchPoll()}
}catch(e){}}
const phone=()=>matchMedia("(max-width:700px)").matches;
function centred(){const p=document.getElementById("pack"),m=p.getBoundingClientRect().left+p.clientWidth/2;let best=null,bd=1e9;
 p.querySelectorAll(".c").forEach(c=>{const r=c.getBoundingClientRect(),d=Math.abs(r.left+r.width/2-m);if(d<bd){bd=d;best=c}});return best}
function follow(){if(!phone())return;const c=centred();if(!c)return;if(sel!==c.dataset.id&&sel!==null)SFX.play("tick");sel=c.dataset.id;const all=[...document.querySelectorAll("#pack .c")];
 document.getElementById("cnt").textContent=(all.indexOf(c)+1)+" / "+all.length;syncBtn()}
document.getElementById("pack").addEventListener("scroll",()=>requestAnimationFrame(follow),{passive:true});
document.addEventListener("click",e=>{if(phone())return;const c=e.target.closest(".c");if(!c)return;sel=c.dataset.id;
 document.querySelectorAll(".c").forEach(x=>x.classList.toggle("sel",x===c&&sel!==PICK));syncBtn()});
document.getElementById("go").onclick=async()=>{if(!sel)return;document.getElementById("go").disabled=true;
 const r=await fetch("/api/pick?k="+K,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:sel})});
 const d=await r.json();if(d.error){SFX.play("error");alert(d.error)}else SFX.play(PICK?"switch":"pick");tick()};

let DK=null,dirty=false,saveT=null,saveMsg="saved ✓";
const BAS=[["Plains","W"],["Island","U"],["Swamp","B"],["Mountain","R"],["Forest","G"]];
const norm=o=>JSON.stringify(Object.keys(o).filter(k=>o[k]>0).sort().map(k=>[k,o[k]]));
const isLand=c=>/\bLand\b/.test(c.type);
function onDone(d){document.body.classList.add("done");
 if(!DK||(!dirty&&(norm(DK.main)!==norm(d.deck.main)||norm(DK.basics)!==norm(d.deck.basics)))){DK={main:{...d.deck.main},basics:{...d.deck.basics}};drawEd()}}
function groups(){const g={};POOLL.forEach(c=>{g[c.id]=g[c.id]||{c,owned:0};g[c.id].owned++});return Object.values(g)}
const TYPES=[["Creature","#e9b44c"],["Instant","#5aa6e8"],["Sorcery","#d9637a"],["Enchantment","#a07ae0"],["Artifact","#9aa3ab"],["Land","#4a9c5d"],["Other","#6b7683"]];
function typeOf(c){const t=c.type.split("—")[0];if(/Creature/.test(t))return"Creature";if(/Land/.test(t))return"Land";if(/Instant/.test(t))return"Instant";if(/Sorcery/.test(t))return"Sorcery";if(/Enchantment/.test(t))return"Enchantment";if(/Artifact/.test(t))return"Artifact";return"Other"}
function stats(){let sp=0,ln=0;const pip={W:0,U:0,B:0,R:0,G:0},curve=[0,0,0,0,0,0,0,0],types={},ptypes={};
 groups().forEach(({c,owned})=>{ptypes[typeOf(c)]=(ptypes[typeOf(c)]||0)+owned;const n=DK.main[c.id]||0;if(!n)return;types[typeOf(c)]=(types[typeOf(c)]||0)+n;if(isLand(c))ln+=n;else{sp+=n;c.colors.forEach(x=>pip[x]+=n);curve[Math.min(7,Math.floor(c.cmc||0))]+=n}});
 const nonb=ln;const bl=Object.values(DK.basics).reduce((a,b)=>a+b,0);ln+=bl;types.Land=(types.Land||0)+bl;return{sp,ln,nonb,total:sp+ln,pip,curve,types,ptypes}}
function costRow(c,n,owned,inDeck){return `<div class="er${inDeck?" in":""} pr" data-pid="${c.id}"><span class="dots">${(c.colors.length?c.colors:["C"]).map(x=>`<i class="d d-${x}"></i>`).join("")}</span>
 <span class="nm">${esc(c.name)} <span class="m">${esc(c.cost)}</span></span><button class="pm" data-act="sub" data-id="${c.id}" ${inDeck?"":"disabled"}>−</button>
 <span class="q">${inDeck}/${owned}</span><button class="pm" data-act="add" data-id="${c.id}" ${inDeck<owned?"":"disabled"}>+</button></div>`}
function typeRows(st){const tot=Math.max(1,st.total),mx=Math.max(1,...TYPES.map(([k])=>Math.max(st.types[k]||0,st.ptypes[k]||0)));
 return TYPES.filter(([k])=>(st.types[k]||0)||(st.ptypes[k]||0)).map(([k,col])=>{const n=st.types[k]||0,pp=st.ptypes[k]||0;
  return `<div class="tr"><span class="tn">${k}</span><span class="tb"><i style="width:${n/mx*100}%;background:${col}"></i>${k==="Land"?"":`<u style="left:${pp/mx*100}%"></u>`}</span><span class="tc">${n} <span class="m">${Math.round(n/tot*100)}%</span></span></div>`}).join("")}
function drawEd(){if(!DK)return;const st=stats(),g=groups();const need=40-st.total;
 const ok=need===0,mx=Math.max(1,...st.curve);if(WASREADY===false&&ok)SFX.play("ready");WASREADY=ok;
 const order=c=>c.colors.length>1?"z":(c.colors[0]||"y");
 const byCmc=g.filter(x=>DK.main[x.c.id]).sort((a,b)=>(isLand(a.c)-isLand(b.c))||(a.c.cmc-b.c.cmc)||a.c.name.localeCompare(b.c.name));
 document.getElementById("ed").innerHTML=`<div id="edbar"><span class="tot ${ok?"ok":"bad"}">${st.total} / 40</span>
  <span class="m">${st.sp} spells · ${st.ln} lands${st.nonb?` (${st.nonb} non-basic)`:""}</span>
  <span class="m">${ok?"ready ✓":need>0?"add "+need:"cut "+(-need)}</span><span class="m" id="sv">${saveMsg}</span>
  <span style="flex:1"></span><button class="sm" data-act="fill">Fill lands to 40</button><button class="sm" data-act="suggest">Reset to suggested</button><button class="sm" data-act="copy">Copy list</button></div>
  <div class="vis"><div class="vc">  <div class="m" style="margin-top:8px">${Object.entries(st.pip).filter(([k,n])=>n>0).map(([k,n])=>`<span style="margin-right:10px"><i class="d d-${k}"></i> ${n}`+"</span>").join("")} <span>mana curve</span></div>
  <div class="curve">${st.curve.map((n,i)=>`<div><i style="height:${Math.round(n/mx*44)}px"></i>${n||""}<br>${i===7?"7+":i}</div>`).join("")}</div></div>
  <div class="vt"><div class="m">card types <span style="opacity:.7">(bar = in deck, tick = in pool)</span></div>${typeRows(st)}</div></div>
  <div class="cols"><div><h3>IN YOUR DECK</h3>${byCmc.map(x=>costRow(x.c,0,x.owned,DK.main[x.c.id])).join("")||"<div class='m'>nothing yet</div>"}
   <h3>BASIC LANDS</h3>${BAS.map(([b,k])=>`<div class="er${DK.basics[b]?" in":""}"><i class="d d-${k}"></i><span class="nm">${b}</span>
    <button class="pm" data-act="bsub" data-b="${b}" ${DK.basics[b]?"":"disabled"}>−</button><span class="q">${DK.basics[b]||0}</span><button class="pm" data-act="badd" data-b="${b}">+</button></div>`).join("")}</div>
  <div><h3>YOUR POOL (${g.reduce((a,x)=>a+x.owned,0)}): tap + to add</h3>${g.sort((a,b)=>order(a.c).localeCompare(order(b.c))||a.c.name.localeCompare(b.c.name)).map(x=>costRow(x.c,0,x.owned,DK.main[x.c.id]||0)).join("")}</div></div><pre id="cp" class="m" style="display:none"></pre>`}
function touch(){dirty=true;saveMsg="saving…";clearTimeout(saveT);saveT=setTimeout(saveDeck,400);drawEd()}
async function saveDeck(){try{const r=await fetch("/api/deck?k="+K,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(DK)});const d=await r.json();
 if(d.error){saveMsg="not saved: "+d.error}else{saveMsg="saved ✓"}}catch(e){saveMsg="not saved (offline?)"}
 dirty=false;const sv=document.getElementById("sv");if(sv)sv.textContent=saveMsg}
function listText(){const L=[];groups().forEach(({c})=>{const n=DK.main[c.id];if(n)L.push(n+" "+c.name)});BAS.forEach(([b])=>{if(DK.basics[b])L.push(DK.basics[b]+" "+b)});return L.join("\n")}
document.addEventListener("click",async e=>{const b=e.target.closest&&e.target.closest("[data-act]");if(!b||!DK)return;const a=b.dataset.act,id=b.dataset.id,bs=b.dataset.b,sc=window.scrollY;
 if(a==="add"){const own=groups().find(x=>x.c.id===id).owned;if((DK.main[id]||0)<own){DK.main[id]=(DK.main[id]||0)+1;SFX.play("add")}else SFX.play("error");touch()}
 else if(a==="sub"){DK.main[id]=Math.max(0,(DK.main[id]||0)-1);SFX.play("remove");touch()}
 else if(a==="badd"){DK.basics[bs]=(DK.basics[bs]||0)+1;SFX.play("add");touch()}
 else if(a==="bsub"){DK.basics[bs]=Math.max(0,(DK.basics[bs]||0)-1);SFX.play("remove");touch()}
 else if(a==="fill"){SFX.play("fill");const st=stats();const need=40-(st.sp+st.nonb);const w=Object.entries(st.pip).filter(([k,n])=>n>0);const tot=w.reduce((x,[k,n])=>x+n,0);
  BAS.forEach(([n])=>DK.basics[n]=0);if(need>0){const nm={W:"Plains",U:"Island",B:"Swamp",R:"Mountain",G:"Forest"};
   if(!tot)DK.basics.Plains=need;else{let left=need;w.sort((a,b)=>b[1]-a[1]).forEach(([k,n],i)=>{const c=i===w.length-1?left:Math.round(need*n/tot);DK.basics[nm[k]]=c;left-=c})}}touch()}
 else if(a==="suggest"){const r=await fetch("/api/deck/reset?k="+K,{method:"POST"});const d=await r.json();if(!d.error){SFX.play("reset");DK={main:{...d.main},basics:{...d.basics}};dirty=false;saveMsg="saved ✓";drawEd()}}
 else if(a==="copy"){const t=listText();try{await navigator.clipboard.writeText(t);SFX.play("copy");saveMsg="list copied ✓";const sv=document.getElementById("sv");if(sv)sv.textContent=saveMsg}catch(x){const p=document.getElementById("cp");p.style.display="block";p.textContent=t;const r=document.createRange();r.selectNodeContents(p);getSelection().removeAllRanges();getSelection().addRange(r)}}
 window.scrollTo(0,sc)});

// ---- "I'm ready" and the match handoff: my own link arrives from /api/match (never anyone else's) once the host publishes it
let RDY=[],MY_READY=false,MATCH={status:"waiting",link:null,note:""},AUTOM=false,RMSG="",LASTR="",MBUSY=false;try{AUTOM=localStorage.getItem("automatch")==="1"}catch(e){}
const lastOpened=()=>{try{return localStorage.getItem("matchopened")||""}catch(e){return ""}};
function openMatch(){const l=MATCH.link;if(!l||!/^https?:\/\//i.test(l))return;const w=window.open(l,"_blank");if(w){try{w.opener=null}catch(e){}}else{RMSG="Your browser blocked the pop-up: press Join the match.";LASTR="";drawRdy()}}
function drawRdy(){if(!DONE)return;const st=DK?stats():null,tot=st?st.total:0,legal=tot===40;
 const strip="Ready: "+RDY.map(p=>p.ready?`<span class="ok">${esc(p.name)} ✓</span>`:`<span class="bld">${esc(p.name)} … (building)</span>`).join(", ")+(MATCH.status==="ready"?"":` <span class="m">· Match starting when everyone is ready…</span>`);
 let h;
 if(MATCH.status==="ready"&&MATCH.link)h=`<span id="rdymsg">${esc(MATCH.note||"Your match is ready.")}</span><label class="m"><input type="checkbox" id="rdyauto" ${AUTOM?"checked":""}> open automatically</label><button id="rdyb" class="join" data-rdy="join">Join the match</button>`;
 else if(MY_READY)h=`<span id="rdymsg">${MATCH.status==="starting"?"Everyone is ready. Match starting…":"Ready ✓ — waiting for others"} <span class="m">(editing your deck cancels ready)</span></span><button class="sm" data-rdy="unready">Unready</button>`;
 else h=`<span id="rdymsg">${RMSG||(legal?"Your deck has 40 cards.":tot>40?"Cut "+(tot-40)+" more to be ready.":"Add "+(40-tot)+" more to be ready.")}</span><button id="rdyb" data-rdy="ready" ${legal?"":"disabled"}>I'm ready</button>`;
 const sig=strip+"|"+h;if(sig===LASTR)return;LASTR=sig;document.getElementById("rdys").innerHTML=strip;document.getElementById("rdyc").innerHTML=h;
 document.title=MATCH.status==="ready"?"▶ Match ready: Star Trek draft":"Star Trek draft"}
async function matchPoll(){if(MBUSY)return;MBUSY=true;try{const m=await (await fetch("/api/match?k="+K,{cache:"no-store"})).json();
 if(m&&m.status){const was=MATCH.link;MATCH=m;if(m.link&&m.link!==was){SFX.play("fanfare");if(AUTOM&&lastOpened()!==m.link){try{localStorage.setItem("matchopened",m.link)}catch(e){}openMatch()}}drawRdy()}}catch(e){}MBUSY=false}
document.getElementById("rdyc").addEventListener("click",async e=>{const b=e.target.closest&&e.target.closest("[data-rdy]");if(!b)return;const a=b.dataset.rdy;
 if(a==="join"){openMatch();return}
 b.disabled=true;if(dirty){clearTimeout(saveT);await saveDeck()}
 try{const r=await fetch("/api/ready?k="+K,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ready:a==="ready"})});const d=await r.json();
  if(d.error){SFX.play("error");RMSG=d.error}else{RMSG="";SFX.play(a==="ready"?"select":"switch");MY_READY=!!d.ready}}catch(x){RMSG="Could not reach the server: try again."}
 LASTR="";drawRdy();tick()});
document.getElementById("rdyc").addEventListener("change",e=>{if(e.target.id==="rdyauto"){AUTOM=e.target.checked;try{localStorage.setItem("automatch",AUTOM?"1":"0")}catch(x){}}});

// ---- sound effects: all synthesised here (WebAudio), no audio files; mute is remembered on this device
const SFX=(()=>{let ctx=null,on=true;try{on=localStorage.getItem("sfx")!=="0"}catch(e){}
 const C=()=>{if(!ctx){try{ctx=new (window.AudioContext||window.webkitAudioContext)()}catch(e){return null}}if(ctx.state==="suspended")ctx.resume();return ctx};
 const env=(c,g,t,a,d,pk)=>{g.gain.setValueAtTime(0.0001,t);g.gain.exponentialRampToValueAtTime(pk,t+a);g.gain.exponentialRampToValueAtTime(0.0001,t+a+d)};
 function tone(c,t,f,d,type,pk,f2){const o=c.createOscillator(),g=c.createGain();o.type=type||"sine";o.frequency.setValueAtTime(f,t);if(f2)o.frequency.exponentialRampToValueAtTime(f2,t+d);
  env(c,g,t,0.008,d,pk||0.15);o.connect(g).connect(c.destination);o.start(t);o.stop(t+d+0.05)}
 function noise(c,t,d,type,f1,f2,pk,q){const n=Math.max(1,Math.floor(c.sampleRate*d)),b=c.createBuffer(1,n,c.sampleRate),a=b.getChannelData(0);for(let i=0;i<n;i++)a[i]=Math.random()*2-1;
  const s=c.createBufferSource();s.buffer=b;const f=c.createBiquadFilter();f.type=type||"bandpass";f.Q.value=q||1;f.frequency.setValueAtTime(f1,t);f.frequency.exponentialRampToValueAtTime(f2,t+d);
  const g=c.createGain();env(c,g,t,Math.min(0.03,d/3),d,pk||0.15);s.connect(f).connect(g).connect(c.destination);s.start(t)}
 const fx={
  tick:(c,t)=>tone(c,t,900,0.04,"square",0.04),
  select:(c,t)=>{tone(c,t,520,0.06,"sine",0.12);tone(c,t+0.05,780,0.07,"sine",0.1)},
  pick:(c,t)=>{noise(c,t,0.07,"lowpass",2600,500,0.3);tone(c,t,200,0.12,"triangle",0.18,90);tone(c,t+0.06,880,0.14,"sine",0.1);tone(c,t+0.12,1320,0.2,"sine",0.07)},
  switch:(c,t)=>{tone(c,t,620,0.05,"triangle",0.12);tone(c,t+0.06,440,0.07,"triangle",0.12)},
  add:(c,t)=>tone(c,t,640,0.08,"triangle",0.14,940),
  remove:(c,t)=>tone(c,t,560,0.08,"triangle",0.12,330),
  fill:(c,t)=>[520,660,880].forEach((f,i)=>tone(c,t+i*0.06,f,0.1,"sine",0.12)),
  reset:(c,t)=>{tone(c,t,700,0.1,"sine",0.1,350)},
  error:(c,t)=>{tone(c,t,150,0.2,"sawtooth",0.12);tone(c,t+0.02,158,0.2,"sawtooth",0.1)},
  save:(c,t)=>tone(c,t,1040,0.16,"sine",0.09),
  copy:(c,t)=>{tone(c,t,880,0.07,"sine",0.1);tone(c,t+0.07,1175,0.12,"sine",0.1)},
  pass:(c,t)=>noise(c,t,0.32,"bandpass",1500,350,0.16,2),
  packopen:(c,t)=>{noise(c,t,0.9,"bandpass",350,3400,0.2,4);noise(c,t+0.05,0.7,"highpass",2500,6000,0.05,1);   // a transporter-style shimmer, then foil
   for(let i=0;i<7;i++)tone(c,t+0.35+i*0.07,1400+Math.random()*2600,0.28,"sine",0.06);
   noise(c,t+0.85,0.12,"highpass",3000,5000,0.14);noise(c,t+0.98,0.1,"highpass",3500,6000,0.1);tone(c,t+0.9,1568,0.4,"sine",0.1)},
  yourpick:(c,t)=>{tone(c,t,1250,0.09,"sine",0.12,1900);tone(c,t+0.11,1500,0.1,"sine",0.12,2300)},
  comm:(c,t)=>{tone(c,t,1400,0.07,"sine",0.1,2100);tone(c,t+0.09,1750,0.09,"sine",0.1,2500)},
  ready:(c,t)=>[523,659,784,1047].forEach((f,i)=>tone(c,t+i*0.09,f,0.28,"triangle",0.13)),
  fanfare:(c,t)=>{[392,523,659,784,1047].forEach((f,i)=>tone(c,t+i*0.11,f,0.45,"triangle",0.13));[262,330,392].forEach(f=>tone(c,t+0.55,f,0.8,"sine",0.08))}};
 return{play(n){if(!on)return;const c=C();if(!c||!fx[n])return;try{fx[n](c,c.currentTime+0.01)}catch(e){}},get on(){return on},
  toggle(){on=!on;try{localStorage.setItem("sfx",on?"1":"0")}catch(e){}if(on)this.play("select");return on}}})();
document.getElementById("sfxb").onclick=e=>{e.target.textContent=SFX.toggle()?"🔊":"🔇"};document.getElementById("sfxb").textContent=SFX.on?"🔊":"🔇";

// ---- table talk: the AI seats' lines (and anything typed), shown and optionally spoken on THIS device
let TTLAST=0,TTOPEN=false,SPEAK=false,TTFIRST=true,SPQ=[],HOSTSAY=false,LOCAL=false;try{SPEAK=localStorage.getItem("voices")==="1"}catch(e){}
function pickVoice(who){const vs=speechSynthesis.getVoices().filter(v=>/^en/i.test(v.lang));
 const want=who==="Claude"?/Moira|Samantha|Karen|Tessa|Serena|Ava|Zoe/i:/Daniel|Oliver|Arthur|Alex|Fred|Rishi/i;return vs.find(v=>want.test(v.name))||vs[0]||null}
function speak(who,text){if(!SPEAK||!window.speechSynthesis||(HOSTSAY&&LOCAL))return;if(SPQ.length>2)return;SPQ.push(1);
 const u=new SpeechSynthesisUtterance(text),v=pickVoice(who);if(v)u.voice=v;u.pitch=who==="Claude"?1.1:0.85;u.rate=who==="Claude"?1.05:0.95;
 u.onend=u.onerror=()=>SPQ.shift();speechSynthesis.speak(u)}
function ttRender(lines){const box=document.getElementById("ttl");let fresh=0;
 lines.forEach(l=>{if(l.id<=TTLAST)return;TTLAST=l.id;const d=document.createElement("div");d.className="tl"+(l.ai?" ai":"");
  d.innerHTML=`<b class="${l.who==="Claude"?"cl":l.who==="Fusion"?"fu":""}">${esc(l.who)}</b> ${esc(l.text)}`;box.appendChild(d);fresh++;
  if(!TTFIRST&&l.ai){SFX.play("comm");setTimeout(()=>speak(l.who,l.text),350)}});
 const wasFirst=TTFIRST;TTFIRST=false;while(box.children.length>60)box.removeChild(box.firstChild);box.scrollTop=box.scrollHeight;
 if(fresh&&!TTOPEN&&!wasFirst)document.getElementById("ttb").classList.add("new")}
async function ttPoll(){try{const d=await (await fetch("/api/banter?k="+K+"&since="+TTLAST,{cache:"no-store"})).json();HOSTSAY=!!d.host_say;LOCAL=!!d.local;const h=document.getElementById("tth");h.style.display=LOCAL?"":"none";h.textContent=HOSTSAY?"🔊 host voice on":"🔇 host voice off";if(d.lines)ttRender(d.lines)}catch(e){}}
async function ttSend(){const i=document.getElementById("tti"),t=i.value.trim();if(!t)return;i.value="";
 const r=await fetch("/api/banter?k="+K,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:t})});const d=await r.json();if(d.error&&r.status!==429)alert(d.error);ttPoll()}
document.getElementById("ttb").onclick=()=>{TTOPEN=!TTOPEN;document.getElementById("tt").classList.toggle("open",TTOPEN);document.getElementById("ttb").classList.remove("new");SFX.play("tick");
 if(TTOPEN){const b=document.getElementById("ttl");b.scrollTop=b.scrollHeight}};
document.getElementById("tts").onclick=ttSend;document.getElementById("tti").addEventListener("keydown",e=>{if(e.key==="Enter")ttSend()});
document.getElementById("ttv").onclick=e=>{SPEAK=!SPEAK;try{localStorage.setItem("voices",SPEAK?"1":"0")}catch(x){}
 e.target.textContent=SPEAK?"🗣 voices on":"🔇 voices off";if(!SPEAK)speechSynthesis.cancel();else speak("Claude","Voices on.")};
document.getElementById("tth").onclick=async()=>{const r=await fetch("/api/say?k="+K,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({on:!HOSTSAY})});const d=await r.json();if(!d.error){HOSTSAY=d.host_say;document.getElementById("tth").textContent=HOSTSAY?"🔊 host voice on":"🔇 host voice off"}};
document.getElementById("ttv").textContent=SPEAK?"🗣 voices on":"🔇 voices off";
document.querySelectorAll("[data-ask]").forEach(b=>b.onclick=async()=>{SFX.play("tick");const r=await fetch("/api/banter/ask?k="+K,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({who:b.dataset.ask})});
 const d=await r.json();if(d.error){b.textContent=d.error;setTimeout(()=>b.textContent="Ask "+b.dataset.ask,1500)}else setTimeout(ttPoll,2500)});
if(window.speechSynthesis)speechSynthesis.getVoices();
setInterval(ttPoll,2000);ttPoll();
const pv=document.getElementById("pv");
function showPv(c,x,y){pv.innerHTML=`<img src="/art/${c.id}?k=${K}" onerror="this.remove()"><div class="n">${esc(c.name)} <span class="m">${esc(c.cost)}</span></div><div class="m">${esc(c.type)}${c.pt?" · "+c.pt:""}</div><div class="t">${esc(c.text)}</div>`;
 pv.style.display="block";if(!phone()){pv.style.left=Math.max(8,Math.min(x+16,innerWidth-320))+"px";pv.style.top=Math.max(8,Math.min(y-40,innerHeight-pv.offsetHeight-8))+"px"}}
document.addEventListener("mousemove",e=>{if(phone())return;const r=e.target.closest&&e.target.closest(".pr");if(r&&POOL[r.dataset.pid])showPv(POOL[r.dataset.pid],e.clientX-340,e.clientY);else pv.style.display="none"});
document.addEventListener("click",e=>{if(!phone())return;if(e.target.closest&&e.target.closest("[data-act]"))return;const r=e.target.closest&&e.target.closest(".pr");if(r&&POOL[r.dataset.pid]){showPv(POOL[r.dataset.pid]);e.stopPropagation()}else pv.style.display="none"},true);
tick();setInterval(tick,1500);
</script></body></html>"""


def make_handler(dr: Draft):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, "application/json", json.dumps(obj).encode())

        def _local(self) -> bool:
            return not any(self.headers.get(h) for h in PROXY_HEADERS)

        def _host_only(self) -> bool:
            """The host's own routes (publish the match, who is ready). Refused unless the connection is the host laptop itself: a
            loopback peer, no proxy/forwarding header (a tunnelled request always carries one), a loopback Host (blocks DNS rebinding),
            and no Origin (blocks a web page in the host's browser from posting here). A valid player token does not change this."""
            try:
                host = urllib.parse.urlsplit("//" + (self.headers.get("Host") or "")).hostname
            except ValueError:
                return False
            return (self.client_address[0] in LOOPBACK and self._local() and host in ("127.0.0.1", "localhost", "::1")
                    and not self.headers.get("Origin"))

        def _seat(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return dr.tokens.get((q.get("k") or [""])[0])

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/":
                return self._send(200, "text/html; charset=utf-8", PAGE.encode())
            if path == "/api/ready/state":               # host only; carries no key and no link
                if not self._host_only():
                    return self._json(403, {"error": "host only"})
                return self._json(200, dr.host_ready_state())
            seat = self._seat()
            if seat is None:
                return self._json(403, {"error": "this draft needs your own link"})
            if path == "/api/banter":
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    last = int((q.get("since") or ["0"])[0])
                except ValueError:
                    last = 0
                return self._json(200, {"lines": dr.banter.since(last), "host_say": dr.voice.enabled, "local": self._local()})
            if path == "/api/match":                     # this seat's own link and status; nobody else's
                return self._json(200, dr.match_for(seat))
            if path == "/api/state":
                with dr.lock:
                    v = dr.view(seat)
                v["art"] = True
                return self._json(200, v)
            if path.startswith("/art/"):
                cid = Path(path[5:]).name
                with dr.lock:
                    mine = {c["id"] for c in dr.picks[seat]} | ({c["id"] for c in dr.current[seat]} if not dr.done else set())
                f = IMAGES / (cid + ".jpg")
                if cid in mine and f.exists():           # only cards this person has been shown: no walking the whole set
                    data = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Cache-Control", "private, max-age=86400")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            self._send(404, "text/plain", b"not found")

        def do_POST(self):
            seat = self._seat()
            n = min(int(self.headers.get("Content-Length", 0) or 0), 65536)
            body = self.rfile.read(n)
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/match/publish":             # host only: a seat token (or none) from anywhere else is refused
                if not self._host_only():
                    return self._json(403, {"error": "host only"})
                if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                    return self._json(415, {"error": "send application/json"})
                try:
                    code, out = dr.publish_match(json.loads(body or b"{}"))
                except ValueError:
                    return self._json(400, {"error": "bad request"})
                return self._json(code, out)
            if seat is None:
                return self._json(403, {"error": "this draft needs your own link"})
            if path == "/api/ready":
                try:
                    flag = json.loads(body or b"{}").get("ready", True)
                except (ValueError, AttributeError):
                    return self._json(400, {"error": "bad request"})
                if not isinstance(flag, bool):
                    return self._json(400, {"error": "ready must be true or false"})
                code, out = dr.set_ready(seat, flag)
                return self._json(code, out)
            if path == "/api/say":                       # the laptop's own switch: a remote seat cannot touch it
                if not self._local():
                    return self._json(403, {"error": "the host voice is switched on the host laptop"})
                try:
                    dr.voice.enabled = bool(json.loads(body or b"{}").get("on"))
                except ValueError:
                    return self._json(400, {"error": "bad request"})
                return self._json(200, {"host_say": dr.voice.enabled})
            if path in ("/api/banter", "/api/banter/ask"):
                try:
                    b = json.loads(body or b"{}")
                except ValueError:
                    return self._json(400, {"error": "bad request"})
                if path == "/api/banter/ask":
                    err = dr.banter.ask(str(b.get("who", "")))
                    return self._json(429 if err else 200, {"error": err} if err else {"ok": True})
                now = time.time()
                if now - dr.chat_at.get(seat, 0) < 1.5:
                    return self._json(429, {"error": "slow down a little"})
                dr.chat_at[seat] = now
                # A typed line is held to the same rule as an AI seat's (2026-11-13: it used to skip the check). The reply says
                # why and never repeats the name back: the name is the thing being kept off the feed, the log and the stream.
                if dr.banter.names_card(str(b.get("text", "")), strict=NAMES[seat] in ("Claude", "Fusion")):
                    return self._json(400, {"error": "Not sent: that line names a card in this set. Picks are hidden while the draft "
                                                     "is on (everyone at the table and on the stream reads this feed), so say it "
                                                     "without the card's name."})
                e = dr.banter.add(NAMES[seat], str(b.get("text", "")), kind="chat")
                return self._json(200 if e else 400, {"ok": True} if e else {"error": "say something first"})
            if path in ("/api/deck", "/api/deck/reset"):
                try:
                    b = json.loads(body or b"{}")
                except ValueError:
                    return self._json(400, {"error": "bad request"})
                code, out = dr.set_deck(seat, b) if path == "/api/deck" else dr.reset_deck(seat)
                return self._json(code, out)
            if path != "/api/pick":
                return self._send(404, "text/plain", b"not found")
            try:
                cid = str(json.loads(body or b"{}").get("id", ""))
            except ValueError:
                return self._json(400, {"error": "bad request"})
            err = dr.pick(seat, cid)
            self._json(400 if err else 200, {"error": err} if err else {"ok": True})
    return H


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=random.SystemRandom().randrange(1, 10**6))
    ap.add_argument("--port", type=int, default=8820)
    ap.add_argument("--keep", action="store_true", help="reuse the links in live/draft-links.txt")
    ap.add_argument("--bot", action="append", default=[], help="a named seat a bot drafts tonight (e.g. --bot Sam); repeatable")
    a = ap.parse_args()
    if a.bot:
        print(f"bots draft for: {', '.join(make_bots(a.bot)) or '(no such seat)'}", flush=True)
    LIVE.mkdir(exist_ok=True)
    links = LIVE / "draft-links.txt"
    state = LIVE / "draft-state.json"
    if a.keep and state.exists():
        a.seed = json.loads(state.read_text())["seed"]
    dr = Draft(a.seed, links if (a.keep and links.exists()) else None)
    if a.keep and state.exists():
        dr.restore(state)
        print(f"restored: pack {dr.rnd + 1}, pick {dr.pick_no + 1}, {len(dr.history)} steps replayed", flush=True)
    links.touch(mode=0o600)
    links.chmod(0o600)
    links.write_text("".join(f"{NAMES[s]}: /?k={t}\n" for t, s in dr.tokens.items() if s in HUMANS))
    ag = LIVE / "draft-agent-tokens.json"
    ag.touch(mode=0o600)
    ag.chmod(0o600)
    ag.write_text(json.dumps({NAMES[s]: t for t, s in dr.tokens.items() if s in AGENTS}))
    print(f"draft seed {a.seed} on http://127.0.0.1:{a.port}/ ; links in {links}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(dr)).serve_forever()
