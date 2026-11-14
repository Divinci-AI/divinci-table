#!/usr/bin/env python3
"""A small tournament for a draft pod: who plays whom each round, the standings, and what the round launcher should start (retro M6).

On 2026-11-13 three rounds were played and the pairings and the standings were in nobody's head but the host's. This keeps them in
a file, pairs the next round, starts it through the round launcher, takes each match's result from the table itself, and publishes
the whole thing as JSON for a stream overlay.

    tournament.py new       --state F --players Michael,Sam,Claude,Fusion [--bots Sam] [--rounds N] [--name "Friday draft"]
    tournament.py pair      --state F                 pair the next round (the current one must be finished); prints the launcher's arguments
    tournament.py start     --state F [--ports 8800,8801] [-- launcher options]     pair if needed, then run launch/play_round.sh for the round
                                                      (--ports once is enough: the tournament remembers its tables' ports; default 8800, 8801, …)
    tournament.py record    --state F                 take the finished matches' results from the tables (GET /api/match on 127.0.0.1)
    tournament.py record    --state F --result Michael:Sam=2-1      …or by hand (a table that is gone); 1-1 is a drawn match
    tournament.py run       --state F [--one-round]   round after round: start, wait until every match is over, record, pair the next
    tournament.py standings --state F [--json]
    tournament.py bot       --state F --player Sam on|off      from the next round on, the autopilot plays that player's seat (Name=bot)
    tournament.py serve     --state F [--port 8890]   GET /tournament.json on 127.0.0.1, for a stream overlay (the same JSON is also
                                                      written beside the state file as <name>-public.json at every change)

The rules it keeps:
  * Standings: match wins, then game wins, then fewer game losses; players level on all three keep their seating order.
    A drawn match is a draw for both. A bye is a match win, 2 games to 0.
  * Pairing ("Swiss"): the players in standings order, and of all the ways to pair them the one with the FEWEST repeated pairings, and
    among those the one that keeps players of like standing together (winners play winners). So a pairing is never repeated while a
    full round can be made without one; with four players three rounds are a round robin. Round 1 pairs in seating order (1v2, 3v4).
  * An odd number of players: the bye goes to the lowest player who has not had one.
  * Rounds: for up to four players everyone plays everyone (3 rounds for four, 3 for three); more players play ceil(log2(n)) rounds.
    --rounds overrides.
  * A result is taken from a table only when that table's two seats ARE the pairing and its match is over. Nothing is guessed.

The state file holds names, pairings, ports and scores: no key, no link, no token. The public JSON holds less (no ports).
Env: PLAY_ROUND (the round launcher; default play_round.sh beside this file), TOURNAMENT_POLL (seconds between looks at the tables: 5).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 never goes through a proxy


# ---------------------------------------------------------------- the state

def new_state(players: list[str], bots=(), rounds: int | None = None, name: str = "") -> dict:
    players = [p.strip() for p in players if p.strip()]
    if len(players) < 2 or len({p.lower() for p in players}) != len(players):
        raise ValueError("a tournament needs two or more players with different names")
    bad = [p for p in players if not p.replace("_", "").isalnum()]
    if bad:
        raise ValueError(f"player names are letters and digits only: {bad}")
    n = len(players)
    planned = rounds or (n - 1 if n % 2 == 0 else n) if n <= 4 else rounds or math.ceil(math.log2(n))
    return {"version": 1, "name": name or "Draft pod", "created": time.strftime("%Y-%m-%d %H:%M:%S"), "players": players,
            "bots": [p for p in players if p.lower() in {b.strip().lower() for b in bots}], "rounds_planned": int(planned), "rounds": []}


def load(path: Path) -> dict:
    st = json.loads(Path(path).read_text())
    if not isinstance(st, dict) or st.get("version") != 1:
        raise ValueError(f"{path} is not a tournament file")
    return st


def save(path: Path, st: dict) -> None:
    """The state (0600, replaced atomically) and, beside it, the public JSON a stream overlay reads (0644)."""
    path = Path(path)
    for f, data, mode in ((path, st, 0o600), (public_path(path), public(st), 0o644)):
        tmp = f.with_name(f.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1)
        os.chmod(tmp, mode)
        os.replace(tmp, f)


def public_path(path: Path) -> Path:
    return Path(path).with_name(Path(path).stem + "-public.json")


# ---------------------------------------------------------------- standings

def standings(st: dict) -> list[dict]:
    rows = {p: {"name": p, "match_wins": 0, "match_losses": 0, "match_draws": 0, "game_wins": 0, "game_losses": 0, "byes": 0, "played": 0,
                "bot": p in st.get("bots", [])} for p in st["players"]}
    for r in st["rounds"]:
        if r.get("bye") in rows:
            b = rows[r["bye"]]
            b["byes"] += 1; b["match_wins"] += 1; b["game_wins"] += 2
        for pr in r["pairings"]:
            res = pr.get("result")
            if not res:
                continue
            a, b = rows[pr["a"]], rows[pr["b"]]
            wa, wb = int(res["wins"].get(pr["a"], 0)), int(res["wins"].get(pr["b"], 0))
            a["played"] += 1; b["played"] += 1
            a["game_wins"] += wa; a["game_losses"] += wb; b["game_wins"] += wb; b["game_losses"] += wa
            if res.get("winner") == pr["a"]:
                a["match_wins"] += 1; b["match_losses"] += 1
            elif res.get("winner") == pr["b"]:
                b["match_wins"] += 1; a["match_losses"] += 1
            else:
                a["match_draws"] += 1; b["match_draws"] += 1
    seat = {p: i for i, p in enumerate(st["players"])}
    out = sorted(rows.values(), key=lambda x: (-x["match_wins"], -x["game_wins"], x["game_losses"], seat[x["name"]]))
    for i, x in enumerate(out):
        same = i and (x["match_wins"], x["game_wins"], x["game_losses"]) == (out[i - 1]["match_wins"], out[i - 1]["game_wins"], out[i - 1]["game_losses"])
        x["place"] = out[i - 1]["place"] if same else i + 1
        x["record"] = f"{x['match_wins']}-{x['match_losses']}" + (f"-{x['match_draws']}" if x["match_draws"] else "")
    return out


# ---------------------------------------------------------------- pairing

def met(st: dict) -> dict:
    """How often each pair has been paired (a round that was paired counts, played or not)."""
    n: dict = {}
    for r in st["rounds"]:
        for pr in r["pairings"]:
            k = frozenset((pr["a"], pr["b"]))
            n[k] = n.get(k, 0) + 1
    return n


def _matchings(names: list):
    """Every way to split an even list into pairs, the first name always paired first (so the order of the results is fixed)."""
    if not names:
        yield []
        return
    a, rest = names[0], names[1:]
    for i, b in enumerate(rest):
        for m in _matchings(rest[:i] + rest[i + 1:]):
            yield [(a, b)] + m


def next_pairings(st: dict) -> tuple[list[tuple[str, str]], str | None]:
    """(pairings, bye) for the next round. See the rules at the top of this file."""
    order = [x["name"] for x in standings(st)]
    rank = {p: i for i, p in enumerate(order)}
    bye = None
    if len(order) % 2:
        byes = {p: sum(1 for r in st["rounds"] if r.get("bye") == p) for p in order}
        bye = next(p for p in reversed(order) if byes[p] == min(byes.values()))     # the lowest player among those with the fewest byes
        order = [p for p in order if p != bye]
    seen = met(st)
    best, best_score = None, None
    for m in _matchings(order):
        score = (sum(seen.get(frozenset(p), 0) for p in m), sum((rank[a] - rank[b]) ** 2 for a, b in m))
        if best_score is None or score < best_score:
            best, best_score = m, score
    return best or [], bye


def current_round(st: dict) -> dict | None:
    return st["rounds"][-1] if st["rounds"] else None


def round_done(r: dict | None) -> bool:
    return bool(r) and all(pr.get("result") for pr in r["pairings"])


def finished(st: dict) -> bool:
    return len(st["rounds"]) >= st["rounds_planned"] and round_done(current_round(st))


def pair(st: dict, ports: list[int] | None = None) -> dict:
    """Add the next round to the state. Refuses while the current round has a match without a result."""
    cur = current_round(st)
    if cur and not round_done(cur):
        raise ValueError(f"round {cur['n']} is not finished: " + ", ".join(f"{p['a']} vs {p['b']}" for p in cur["pairings"] if not p.get("result")))
    if len(st["rounds"]) >= st["rounds_planned"]:
        raise ValueError(f"all {st['rounds_planned']} rounds are played (tournament.py new --rounds N for more)")
    pairs, bye = next_pairings(st)
    seen = met(st)
    if ports:
        st["ports"] = list(ports)                      # the tables' ports are the tournament's: later rounds use them without being told again
    ports = st.get("ports") or []
    r = {"n": len(st["rounds"]) + 1, "bye": bye, "started": None, "paired": time.strftime("%Y-%m-%d %H:%M:%S"),
         "pairings": [{"a": a, "b": b, "port": (ports[i] if ports and i < len(ports) else 8800 + i), "result": None,
                       **({"repeat": True} if seen.get(frozenset((a, b))) else {})} for i, (a, b) in enumerate(pairs)]}
    st["rounds"].append(r)
    return r


def launcher_args(st: dict, r: dict) -> list[str]:
    """What launch/play_round.sh is given for a round: --ports and one Name:Name per table (Name=bot for a player the autopilot plays)."""
    bots = set(st.get("bots", []))
    side = lambda p: p + ("=bot" if p in bots else "")      # noqa: E731
    return ["--ports", ",".join(str(pr["port"]) for pr in r["pairings"]), *[f"{side(pr['a'])}:{side(pr['b'])}" for pr in r["pairings"]]]


# ---------------------------------------------------------------- results

def set_result(st: dict, a: str, b: str, wins_a: int, wins_b: int, source: str = "hand", games: int | None = None) -> dict:
    r = current_round(st)
    low = {p.lower(): p for p in st["players"]}
    a, b = low.get(a.lower()), low.get(b.lower())
    pr = next((p for p in (r or {}).get("pairings", []) if {p["a"], p["b"]} == {a, b}), None) if a and b else None
    if not pr:
        raise ValueError("that pairing is not in the current round")
    wins = {a: int(wins_a), b: int(wins_b)}
    winner = a if wins_a > wins_b else b if wins_b > wins_a else None
    pr["result"] = {"winner": winner, "wins": {pr["a"]: wins[pr["a"]], pr["b"]: wins[pr["b"]]}, "draw": winner is None,
                    "games": games if games is not None else wins_a + wins_b, "source": source, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    return pr


def table_match(port: int) -> dict | None:
    """What the table on a port says about its match (public, no key), or None when it does not answer. A single-game table is reported
    as a one-game match once its game is over."""
    def get(path):
        try:
            with LOCAL.open(f"http://127.0.0.1:{port}{path}", timeout=4) as x:
                return json.loads(x.read() or b"{}")
        except (urllib.error.URLError, OSError, ValueError):
            return None
    m = get("/api/match")
    if m is None:
        return None
    if m.get("on"):
        return m
    g = get("/api/game/summary") or {}
    seats = sorted((g.get("totals") or {}).keys())
    return {"on": True, "single": True, "seats": seats, "over": bool(g.get("over")), "winner": g.get("winner"),
            "wins": {s: int(s == g.get("winner")) for s in seats}, "games": [g] if g.get("over") else []}


def record_from_tables(st: dict, say=print) -> int:
    """Take the result of every finished match of the current round from its table. Returns how many were recorded now."""
    r = current_round(st)
    n = 0
    for pr in (r or {}).get("pairings", []):
        if pr.get("result"):
            continue
        m = table_match(int(pr["port"]))
        if m is None:
            continue
        if {s.lower() for s in m.get("seats") or []} != {pr["a"].lower(), pr["b"].lower()}:
            say(f"table :{pr['port']} is not {pr['a']} vs {pr['b']} (it seats {' and '.join(m.get('seats') or []) or 'nobody'}): no result taken from it")
            continue
        if not m.get("over"):
            continue
        wins = {s.lower(): int(v) for s, v in (m.get("wins") or {}).items()}
        set_result(st, pr["a"], pr["b"], wins.get(pr["a"].lower(), 0), wins.get(pr["b"].lower(), 0), source="table", games=len(m.get("games") or []))
        res = pr["result"]
        say(f"round {r['n']}: {pr['a']} vs {pr['b']}: " + (f"{res['winner']} wins {max(res['wins'].values())}-{min(res['wins'].values())}" if res["winner"]
                                                        else f"a drawn match {res['wins'][pr['a']]}-{res['wins'][pr['b']]}"))
        n += 1
    return n


# ---------------------------------------------------------------- what everyone may read

def public(st: dict) -> dict:
    """For a stream overlay: names, pairings, scores, standings. No port, no path, no key (there is none in the state to begin with)."""
    def one(pr):
        res = pr.get("result")
        return {"a": pr["a"], "b": pr["b"], "done": bool(res), "winner": (res or {}).get("winner"), "draw": bool((res or {}).get("draw")),
                "wins": dict((res or {}).get("wins") or {}), "score": (f"{res['wins'][pr['a']]}-{res['wins'][pr['b']]}" if res else ""),
                **({"repeat": True} if pr.get("repeat") else {})}
    cur = current_round(st)
    return {"name": st.get("name") or "", "players": [{"name": p, "bot": p in st.get("bots", [])} for p in st["players"]],
            "rounds_planned": st["rounds_planned"], "round": cur["n"] if cur else 0, "round_done": round_done(cur), "finished": finished(st),
            "rounds": [{"n": r["n"], "bye": r.get("bye"), "pairings": [one(p) for p in r["pairings"]]} for r in st["rounds"]],
            "standings": [{k: x[k] for k in ("place", "name", "record", "match_wins", "match_losses", "match_draws", "game_wins", "game_losses", "byes", "bot")}
                          for x in standings(st)],
            "updated": time.strftime("%Y-%m-%d %H:%M:%S")}


def standings_text(st: dict) -> str:
    rows = standings(st)
    w = max(len(x["name"]) for x in rows)
    return "\n".join(f"{x['place']}. {x['name']:<{w}}  {x['record']:<6} games {x['game_wins']}-{x['game_losses']}" + ("  (autopilot)" if x["bot"] else "")
                     + (f"  bye x{x['byes']}" if x["byes"] else "") for x in rows)


def round_text(r: dict) -> str:
    return (f"round {r['n']}: " + "; ".join(f"{p['a']} vs {p['b']} on :{p['port']}" + (" (a repeat: no round could be made without one)" if p.get("repeat") else "")
                                           for p in r["pairings"]) + (f"; bye: {r['bye']}" if r.get("bye") else ""))


# ---------------------------------------------------------------- commands

def cmd_new(a) -> int:
    f = Path(a.state)
    if f.exists() and not a.force:
        print(f"{f} exists (a tournament in progress?): --force starts over")
        return 1
    try:
        st = new_state(a.players.split(","), (a.bots or "").split(","), a.rounds, a.name or "")
    except ValueError as e:
        print(e)
        return 1
    save(f, st)
    print(f"{st['name']}: {', '.join(st['players'])}; {st['rounds_planned']} rounds; state in {f}, public JSON in {public_path(f)}")
    return 0


def ensure_round(st: dict, ports) -> dict:
    cur = current_round(st)
    return cur if cur and not round_done(cur) else pair(st, ports)


def cmd_pair(a) -> int:
    st = load(a.state)
    try:
        r = pair(st, [int(p) for p in a.ports.split(",")] if a.ports else None)
    except ValueError as e:
        print(e)
        return 1
    save(a.state, st)
    print(round_text(r))
    print("launcher: " + " ".join(launcher_args(st, r)))
    return 0


def start_round(st: dict, state_path, ports=None, extra=(), say=print) -> int:
    try:
        r = ensure_round(st, ports)
    except ValueError as e:
        say(str(e))
        return 1
    save(state_path, st)
    say(round_text(r))
    if not r["pairings"]:
        return 0
    launcher = os.environ.get("PLAY_ROUND") or str(HERE / "play_round.sh")
    rc = subprocess.run([launcher, *extra, *launcher_args(st, r)]).returncode
    if rc not in (0, 4):                               # (4: the tables are up, the remote player's tunnel is not proved: the round is on)
        say(f"the round launcher exited {rc}: round {r['n']} is paired but NOT started (run `start` again when it is fixed)")
        return rc
    r["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(state_path, st)
    return rc


def cmd_start(a) -> int:
    st = load(a.state)
    extra = a.extra[1:] if a.extra[:1] == ["--"] else a.extra
    return start_round(st, a.state, [int(p) for p in a.ports.split(",")] if a.ports else None, extra)


def cmd_record(a) -> int:
    st = load(a.state)
    if a.result:
        try:
            names, score = a.result.split("=")
            x, y = names.split(":")
            wa, wb = score.split("-")
            pr = set_result(st, x, y, int(wa), int(wb))
        except ValueError as e:
            print(f"--result wants Name:Name=N-N for a pairing of the current round ({e})")
            return 1
        print(f"recorded by hand: {pr['a']} vs {pr['b']}: {pr['result']['wins'][pr['a']]}-{pr['result']['wins'][pr['b']]}")
    else:
        record_from_tables(st)
    save(a.state, st)
    cur = current_round(st)
    if cur and not round_done(cur):
        print(f"round {cur['n']} still playing: " + ", ".join(f"{p['a']} vs {p['b']}" for p in cur["pairings"] if not p.get("result")))
    elif cur:
        print(f"ROUND {cur['n']} DONE\n" + standings_text(st))
    return 0


def cmd_run(a) -> int:
    poll = float(os.environ.get("TOURNAMENT_POLL") or 5)
    say = lambda s: print(s, flush=True)               # noqa: E731
    extra = a.extra[1:] if a.extra[:1] == ["--"] else a.extra
    while True:
        st = load(a.state)
        if finished(st):
            say("TOURNAMENT DONE\n" + standings_text(st))
            return 0
        cur = current_round(st)
        if not cur or round_done(cur) or not cur.get("started"):
            rc = start_round(st, a.state, [int(p) for p in a.ports.split(",")] if a.ports else None, extra, say)
            if rc not in (0, 4):
                return rc
            st = load(a.state)
            cur = current_round(st)
        t0 = time.time()
        while not round_done(cur):
            if record_from_tables(st, say):
                save(a.state, st)
            if round_done(cur):
                break
            if a.max_secs and time.time() - t0 > a.max_secs:
                say(f"round {cur['n']} is not finished after {a.max_secs:g} s: stopping here (run again to go on)")
                return 2
            time.sleep(poll)
        save(a.state, st)
        say(f"ROUND {cur['n']} DONE: " + "; ".join(f"{p['a']} {p['result']['wins'][p['a']]}-{p['result']['wins'][p['b']]} {p['b']}" for p in cur["pairings"])
            + (f"; bye: {cur['bye']}" if cur.get("bye") else ""))
        say(standings_text(st))
        if a.one_round and not finished(st):
            nxt, bye = next_pairings(st)
            say("next round: " + "; ".join(f"{x} vs {y}" for x, y in nxt) + (f"; bye: {bye}" if bye else "") + "  (run again to start it)")
            return 0


def cmd_standings(a) -> int:
    st = load(a.state)
    print(json.dumps(public(st), indent=1) if a.json else standings_text(st))
    return 0


def cmd_bot(a) -> int:
    st = load(a.state)
    p = next((x for x in st["players"] if x.lower() == a.player.lower()), None)
    if not p:
        print(f"no player {a.player}")
        return 1
    st["bots"] = [x for x in st.get("bots", []) if x != p] + ([p] if a.state_ == "on" else [])
    save(a.state, st)
    print(f"{p}: " + ("the autopilot plays this seat from the next round on (the table will say so)" if a.state_ == "on" else "plays in person again"))
    return 0


def make_handler(state_path: Path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
            if host not in ("127.0.0.1", "localhost", "::1"):       # a page elsewhere pointing a name at this laptop gets nothing
                body, code = b'{"error": "this laptop only"}', 403
            elif self.path.split("?")[0] not in ("/", "/tournament.json"):
                body, code = b'{"error": "GET /tournament.json"}', 404
            else:
                try:
                    body, code = json.dumps(public(load(state_path))).encode(), 200
                except (OSError, ValueError):
                    body, code = b'{"error": "no tournament"}', 404
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")     # public information, read by an overlay page on another local port
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return H


def cmd_serve(a) -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(Path(a.state)))
    print(f"standings on http://127.0.0.1:{srv.server_address[1]}/tournament.json", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def P(name):
        p = sub.add_parser(name)
        p.add_argument("--state", required=True)
        return p
    p = P("new"); p.add_argument("--players", required=True); p.add_argument("--bots", default=""); p.add_argument("--rounds", type=int)
    p.add_argument("--name", default=""); p.add_argument("--force", action="store_true")
    p = P("pair"); p.add_argument("--ports", default="")
    p = P("start"); p.add_argument("--ports", default=""); p.add_argument("extra", nargs=argparse.REMAINDER)
    p = P("record"); p.add_argument("--result", default="")
    p = P("run"); p.add_argument("--ports", default=""); p.add_argument("--one-round", action="store_true"); p.add_argument("--max-secs", type=float, default=0.0)
    p.add_argument("extra", nargs=argparse.REMAINDER)
    p = P("standings"); p.add_argument("--json", action="store_true")
    p = P("bot"); p.add_argument("--player", required=True); p.add_argument("state_", choices=["on", "off"])
    p = P("serve"); p.add_argument("--port", type=int, default=8890)
    a = ap.parse_args(argv)
    return {"new": cmd_new, "pair": cmd_pair, "start": cmd_start, "record": cmd_record, "run": cmd_run, "standings": cmd_standings, "bot": cmd_bot,
            "serve": cmd_serve}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
