#!/usr/bin/env python3
"""One two-seat game on the REAL table server, each AI seat played by table/fusion_brain.py.

  --dry            no model: every decision takes option 1 (smoke test of the plumbing; needs no Jetson)
  default          FUSION_BACKEND=local: gpt-oss-20b via JETSON_BASE (the loopback end of ssh -L), no silent fallback
Stops at --turns, a game-over event, a failed pilot (exit 3), the STOP file, or --max-secs; saves ALL events (paged).
Prints a summary; never prints server.log (it records private peeks).
"""
import argparse, json, os, signal, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TABLE = REPO / "table"
DEFAULT_DECKS = "kaust,tuvasa"
SEATS: dict = {}                                          # seat name -> (commander, deck path), set by main() from --decks


def seats_for(decks: str) -> dict:
    """'kaust,tuvasa,elsha' -> {'Kaust': ('Kaust, Eyes of the Glade', 'decks/kaust.json'), ...}: a seat is named for its commander's first word."""
    out = {}
    for name in [x.strip() for x in decks.split(",") if x.strip()]:
        path = f"decks/{name}.json"
        d = json.loads((REPO / path).read_text())
        c = d["commander"][0] if isinstance(d["commander"], list) else d["commander"]
        cmdr = c["name"] if isinstance(c, dict) else c
        words = "".join(ch if ch.isalnum() or ch in " ," else "" for ch in cmdr.split(",")[0]).split()
        seat = (words[1] if words[0] in ("Captain", "The") and len(words) > 1 else words[0]) if words else name.title()
        while seat in out:
            seat += "2"
        out[seat] = (cmdr, path)
    if not 2 <= len(out) <= 6:
        raise SystemExit("--decks needs 2 to 6 decks")
    return out


def http(base, method, path, body=None, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Brain-Token"] = token
    r = urllib.request.Request(base + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=30) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def all_events(base):
    evs, since = [], 0
    while True:
        _, page = http(base, "GET", f"/api/events?since={since}")
        evs += page.get("events", [])
        if len(page.get("events", [])) < 300:
            return evs
        since = page["events"][-1]["id"]


def lives(evs):
    """The last public life total of each player."""
    out = {}
    for e in evs:
        if e.get("type") == "life" and isinstance(e.get("life"), int):
            out[e.get("player")] = e["life"]
    return out


def keepalive(root, stats, stop):
    """GET <router>/health every 2 s through the tunnel while the game runs: cheap (no model call), keeps the path busy,
    and records whether the path ever stalls. Not an Orin request of any weight."""
    while not stop.is_set():
        t = time.time()
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(root + "/health", timeout=5).read()
            stats["ok"] += 1
        except Exception:                                    # noqa: BLE001
            stats["failed"] += 1
        stats["max_s"] = max(stats["max_s"], round(time.time() - t, 3))
        stop.wait(2)


def snapshots(base, path, stop):
    """The PUBLIC board once a second (/api/board3d: no token, no hands) into state.jsonl, for the oracle's battlefield checks."""
    while not stop.is_set():
        try:
            with urllib.request.urlopen(base + "/api/board3d", timeout=5) as f:
                d = json.loads(f.read())
            rec = {"ts": round(time.time(), 2), "seats": {x["name"]: {
                "life": x.get("life"), "hand": x.get("hand"), "library": x.get("library"), "gy": x.get("graveyard_count"),
                "cmdr": x.get("commander"), "cmdr_in_zone": x.get("commander_in_zone"),
                "perms": [[q.get("id"), q.get("name"), q.get("type"), q.get("pt"), q.get("tapped"), q.get("counters"), q.get("token"), q.get("attached_to")]
                          for q in x.get("permanents", [])]} for x in d.get("seats", [])}}
            with open(path, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception:                                    # noqa: BLE001 — a missed sample is fine
            pass
        stop.wait(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run"); ap.add_argument("--port", type=int, default=8861); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--turns", type=int, default=6); ap.add_argument("--max-secs", type=int, default=900)
    ap.add_argument("--decks", default=DEFAULT_DECKS, help="2-6 deck names from decks/, in seating order (default kaust,tuvasa)")
    ap.add_argument("--dry", action="store_true"); ap.add_argument("--stop-file", default=str(HERE / "results" / "STOP"))
    a = ap.parse_args()
    SEATS.clear(); SEATS.update(seats_for(a.decks))
    first = next(iter(SEATS))
    os.umask(0o077)                                           # every file this run and its children create is private
    run = Path(a.run or tempfile.mkdtemp(prefix="pilot-")).resolve(); run.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{a.port}"; tok = run / "host-token"; order = ",".join(SEATS)
    os.chmod(run, 0o700)                                      # server.log records private peeks
    # The children get an ALLOWLIST, not this shell's environment (which may hold cloud keys and tokens).
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV", "PYTHONIOENCODING")}
    env.update(TABLE_CLOUD="1", TABLE_HIGHROLL="table", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template",
               TABLE_RESEARCH_DIR=str(run / "research"))
    for k in ("JETSON_BASE", "PILOT_MODEL"):                  # the model route is the only thing from outside that the brains need
        if os.environ.get(k) and not a.dry:
            env[k] = os.environ[k]
    cmd = [sys.executable, str(TABLE / "server.py"), "--any-card", "--port", str(a.port), "--brain", "external", "--token-file", str(tok), "--seed", str(a.seed)]
    for seat, (cmdr, deck) in SEATS.items():
        cmd += ["--ai", f"{seat}|{cmdr}|", "--ai-deck", str(REPO / deck)]
    cmd += ["--order", order, "--priority-window", "0.2"]
    procs = [subprocess.Popen(cmd, cwd=REPO, env=env, stdout=open(run / "server.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)]
    brains, reason = [], "unknown"
    hstats, hstop = {"ok": 0, "failed": 0, "max_s": 0.0}, threading.Event()
    if not a.dry and os.environ.get("JETSON_BASE"):
        threading.Thread(target=keepalive, args=(os.environ["JETSON_BASE"].rstrip("/").removesuffix("/v1"), hstats, hstop), daemon=True).start()
    game_over_http = None
    threading.Thread(target=snapshots, args=(base, run / "state.jsonl", hstop), daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit("terminated"))   # the finally below must run: no orphaned server or brains
    try:
        for _ in range(240):
            if procs[0].poll() is not None:
                sys.exit(f"server exited (see {run / 'server.log'})")
            try:
                urllib.request.urlopen(base + "/api/phase", timeout=2).read(); break
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        benv = dict(env)
        if not a.dry:
            benv.update(FUSION_BACKEND="local", PILOT_LOG=str(run / "pilot-decisions.jsonl"), PILOT_GAME=run.name)
            if not benv.get("JETSON_BASE"):
                sys.exit("JETSON_BASE is not set (the loopback end of the ssh -L tunnel, e.g. http://127.0.0.1:18081/v1)")
        for seat in SEATS:
            bcmd = [sys.executable, str(TABLE / "fusion_brain.py"), "--seat", seat, "--url", base, "--token-file", str(tok)] + (["--dry"] if a.dry else [])
            brains.append(subprocess.Popen(bcmd, cwd=REPO, env=benv, stdout=open(run / f"brain-{seat}.log", "w"), stderr=subprocess.STDOUT, start_new_session=True))
        for _ in range(120):                                  # both brains must be LISTENING before the roll: a "your turn" that goes
            if all("watching from" in (run / f"brain-{x}.log").read_text() for x in SEATS): break   # by unheard hangs a dry game
            if any(b.poll() is not None for b in brains): sys.exit("a brain exited before the game started")
            time.sleep(0.5)
        else:
            sys.exit("the brains never started listening")
        token = tok.read_text().strip()
        code, r = http(base, "POST", "/api/highroll/start", {"mode": "table", "sides": 20, "by": first}, token)
        (run / "highroll.json").write_text(json.dumps([code, r]))
        t0 = time.time()
        while True:
            evs = all_events(base)
            turns = sum(1 for e in evs if e.get("type") == "attention" and e.get("kind") == "turn")
            if any(e.get("type") == "game-over" for e in evs): reason = "game-over"; break
            lv = lives(evs)
            out_ = [x for x in SEATS if lv.get(x) is not None and lv[x] <= 0]
            alive = [x for x in SEATS if x not in out_]
            if out_ and len(alive) <= 1:                      # we are the host here: the table leaves "the game is over" to its host
                game_over_http, _ = http(base, "POST", "/api/game-over", {"order": alive + out_, "winner": alive[0] if alive else out_[0]}, token)
                reason = f"game-over (eliminated: {', '.join(out_)})"; break
            if turns >= a.turns: reason = f"turn cap ({a.turns})"; break
            if any(b.poll() is not None for b in brains): reason = f"a brain exited (code {[b.poll() for b in brains]})"; break
            if Path(a.stop_file).exists(): reason = "STOP file"; break
            if time.time() - t0 > a.max_secs: reason = "max-secs"; break
            time.sleep(3)
        evs = all_events(base)
        (run / "events.json").write_text(json.dumps({"events": evs}))
        brain_codes = [b.poll() for b in brains]
    finally:
        hstop.set()
        for p in brains + procs:
            if p.poll() is None:
                p.terminate()
        for p in brains + procs:
            try: p.wait(10)
            except subprocess.TimeoutExpired: p.kill()
    refused = sum(1 for e in evs if "REFUSED" in json.dumps(e))
    log = run / "pilot-decisions.jsonl"
    dec = [json.loads(l) for l in open(log)] if log.exists() else []
    calls = [d for d in dec if "latency_s" in d and "error" not in d]
    lat = sorted(d["latency_s"] for d in calls)
    tok = lambda k: [d[k] for d in calls if d.get(k) is not None]
    pt = sorted(tok("prompt_tokens"))
    sys.path.insert(0, str(HERE))
    import oracle
    try:
        orc = oracle.run(run / "research", [REPO / d[1] for d in SEATS.values()], state=run / "state.jsonl")
    except SystemExit as e:                                  # no research folder: say so, do not hide it
        orc = {"error": str(e), "by_kind": {}, "signatures": {}}
    (run / "oracle.json").write_text(json.dumps(orc, indent=1))
    failed = [x for x in SEATS if "PILOT-FAILED" in (run / f"brain-{x}.log").read_text()]
    table_findings = sum(n for k, n in (orc.get("by_kind") or {}).items() if k != "pilot-error")
    rc = 3 if failed else (4 if orc.get("error") else (2 if table_findings else 0))
    print(json.dumps({"run": str(run), "stopped": reason, "exit": rc, "pilot_failed": failed, "oracle": orc.get("by_kind"), "oracle_signatures": orc.get("signatures"), "events": len(evs), "turn_events": turns, "seats": list(SEATS), "decks": a.decks, "highroll_http": code, "game_over_http": game_over_http, "path_health": hstats if hstats["ok"] + hstats["failed"] else None,
                      "decisions": len(dec), "model_calls": len(calls), "skipped_forced": sum(1 for d in dec if d.get("skipped")),
                      "errors": sum(1 for d in dec if "error" in d), "option_counts": sorted({d.get("options") for d in dec if d.get("options")}),
                      "prompt_tokens_p50": pt[len(pt) // 2] if pt else None, "completion_tokens_total": sum(tok("completion_tokens")),
                      "reasoning_tokens_total": sum(tok("reasoning_tokens")), "choices": [d["choice"] for d in calls],
                      "p50_s": lat[len(lat) // 2] if lat else None, "max_s": lat[-1] if lat else None}, indent=1))
    sys.exit(rc)


if __name__ == "__main__":
    main()
