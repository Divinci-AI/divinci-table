#!/usr/bin/env python3
"""The tests a fresh clone can run: plain python3, no Gemma/Ollama, no Playwright, no microphone, no hardware, and only
free ports (it never touches 8800/8801/8820/8830/8883, so it is safe beside a live game).

    python3 table/tests/run_core.py                 everything portable, 3 at a time
    python3 table/tests/run_core.py retro_round4    only tests whose file name contains this
    python3 table/tests/run_core.py -j 6 -t 240     6 at a time, 240 s per test
    python3 table/tests/run_core.py --shard 2/4     the second quarter of the list (every 4th file, by name): a full run in pieces

Run it with the interpreter you installed table/requirements.txt into (numpy; docs/testing.md lists what else a few tests use). The
first lines say which interpreter runs the tests and what it lacks. Nothing is ever downloaded: every test runs with TABLE_OFFLINE=1.

A test that needs something this clone does not have (the Star Trek card data, the Claude bot, an older frozen table)
exits 77 = SKIPPED, with the one command that fixes it. Skipped is not failed, but the summary says exactly what was skipped.
Exit 0 = nothing failed. For the card data first run:  python3 table/fetch_data.py --set trk
(run_all.sh is the author's full run: Gemma, the browser suites and a live-table workflow; it needs a Mac with that setup.)"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent.parent

# Not run here, each for a stated reason. (Their own docstrings say how to run them.)
EXCLUDE = {
    r"playtest2_server|pilot_e2e|e2e_offline|brain_e2e|ai_turn_e2e|game_sim|sense_run|network_rehearsal": "binds the owner's ports (8800/8801/8820/8830/8883) or drives a live game",
    r"openmic|speaker|voice|solo_rehearsal|survey_run|route_eval|vision_eval": "needs Gemma/Ollama, Whisper or a microphone",
    r"camera|gantry_camera|stream_supervisor|broadcast_leak": "needs OpenCV or Playwright, or the physical table",
}

# Failing on the published commit and on the one before it, each for a reason that is the test's, not a regression: they are listed
# here, with what is known, so a fresh clone's run shows them as expected and goes red only for something NEW. A test that starts
# passing is reported so its line can be deleted. (Fix the test, then remove the line: this is a to-do list, not a place to hide.)
KNOWN_RED = {
    "combat_server_test.py": "17 of 72 checks are the pre-v6c combat model (the table dealt damage at once on 'I take it'); v6c asks every defender for an explicit block answer and deals damage at the damage step. combat_v6_test covers the new model; this file needs rewriting against it, not patching",
}


def candidates(filt: str):
    for p in sorted(TESTS.glob("*.py")):
        if not (p.name.endswith("_test.py") or p.name in ("engine_rules.py",)):
            continue
        if filt and filt not in p.name:
            continue
        yield p
    for p in sorted((REPO / "draft").glob("test_*.py")):      # the draft engine, deck builder and "I'm ready" handoff
        if not filt or filt in p.name:
            yield p
    for p in sorted((REPO / "launch").glob("test_*.py")):     # the round launchers, tunnels and the tournament driver
        if not filt or filt in p.name:
            yield p
    for p in sorted((REPO / "cloud" / "test").glob("*.py")):    # the public lobby: the room host, and a local lobby in front of it
        if not filt or filt in p.name:
            yield p


def run_one(p: Path, timeout: int):
    tmp = tempfile.mkdtemp(prefix="core-")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "NO_GEMMA": "1", "HF_HUB_OFFLINE": "1", "ROUTER": "code", "REPLIES": "template", "TABLE_NO_VOICE": "1",
           "TABLE_OFFLINE": "1",                             # nothing a test starts may download (the table, oracle.py and fetch_data.py all refuse)
           "TABLE_RESEARCH_DIR": tmp + "/research", "TABLE_SAVES_DIR": tmp + "/saves"}
    env.pop("TYPESAFE_API_KEY", None)
    t0 = time.time()
    proc = subprocess.Popen([sys.executable, str(p)], cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):          # the process group this test started, by its leader's pid
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                out, _ = proc.communicate(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        rc = "timeout"
    shutil.rmtree(tmp, ignore_errors=True)
    lines = [l for l in (out or "").strip().splitlines() if l.strip()]
    status = "skip" if rc == 77 else "pass" if rc == 0 else "timeout" if rc == "timeout" else "fail"
    if status == "fail" and p.name in KNOWN_RED:
        status = "known"
    why = ""
    if status == "skip":
        m = re.search(r"SKIP: (.*)", out or "")
        why = m.group(1) if m else ""
    return {"name": p.name, "status": status, "secs": round(time.time() - t0, 1), "why": why, "tail": lines[-4:]}


# What the suite uses beyond the standard library: module -> (what to install, what happens without it). table/requirements.txt is the
# reference; this is what the tests run HERE actually import, directly or through the table they start.
NEEDS = {
    "numpy": ("numpy", "the table does not start without it (table/voice.py imports it): every test that starts a table will FAIL"),
    "chess": ("chess", "cloud/test/roomhost_test.py will SKIP (one of its rooms is a chess room)"),
    "Vision": ("pyobjc-framework-Vision pyobjc-framework-Cocoa",
               "restore_old_snapshot_test.py will SKIP (the frozen older table it starts imports the Mac text reader at start-up; today's table does not)"),
}


def preflight() -> None:
    """Say, before anything runs, which interpreter runs the tests, what it lacks, and whether the card data is here."""
    import importlib.util
    need = {m: v for m, v in NEEDS.items() if m != "Vision" or sys.platform == "darwin"}
    missing = [m for m in need if importlib.util.find_spec(m) is None]
    print(f"interpreter: {sys.executable} (Python {sys.version.split()[0]}); every test is run with it" +
          ("; it has what the suite uses (" + ", ".join(need) + ")" if not missing else ""), flush=True)
    for m in missing:
        print(f"   MISSING {m}: {need[m][1]}. Fix: {sys.executable} -m pip install {need[m][0]}"
              "   (or run this with the interpreter you installed table/requirements.txt into)", flush=True)
    cache = REPO / "table" / ".cache"
    if not (cache / "oracle.json").is_file():
        print("card data: table/.cache/oracle.json is not here. Nothing is downloaded during a test (TABLE_OFFLINE=1), so every test that "
              "needs the card data will SKIP. Get it once, with network access: python3 table/fetch_data.py --set trk   (about 40 MB)", flush=True)


def shard(items: list, spec: str) -> list:
    """--shard I/N: every N-th item starting at the I-th (1-based), in the list's own order: N runs cover everything exactly once."""
    m = re.fullmatch(r"(\d+)/(\d+)", spec or "")
    if not m or not 1 <= int(m.group(1)) <= int(m.group(2)):
        sys.exit(f"--shard wants I/N with 1 <= I <= N (for example 2/4), not {spec!r}")
    i, n = int(m.group(1)), int(m.group(2))
    return [x for k, x in enumerate(items) if k % n == i - 1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("filter", nargs="?", default="")
    ap.add_argument("-j", type=int, default=3, help="tests at once")
    ap.add_argument("-t", type=int, default=600, help="seconds per test (retro_game4_test alone takes about 5 minutes)")
    ap.add_argument("--shard", default="", metavar="I/N", help="run only part I of N (every N-th test file by name): N runs cover the whole suite")
    a = ap.parse_args()
    preflight()
    run, skipped_here = [], []
    for p in candidates(a.filter):
        reason = next((why for rx, why in EXCLUDE.items() if re.search(rx, p.name)), None)
        (skipped_here if reason else run).append((p, reason))
    total = len(run)
    if a.shard:
        run = shard(run, a.shard)
        print(f"shard {a.shard}: {len(run)} of {total} test files", flush=True)
    results = []
    with cf.ThreadPoolExecutor(max_workers=a.j) as ex:
        for r in ex.map(lambda pr: run_one(pr[0], a.t), run):
            results.append(r)
            mark = {"pass": "✅", "skip": "⏭️ ", "fail": "❌", "timeout": "⏱️ ", "known": "🟡"}[r["status"]]
            note = r["why"] or (f"known red: {KNOWN_RED[r['name']]}" if r["status"] == "known" else "")
            if r["status"] == "pass" and r["name"] in KNOWN_RED:
                note = "now passes: delete its KNOWN_RED line"
            print(f"{mark} {r['name']:<34}{r['secs']:>7}s" + (f"   {note}" if note else ""), flush=True)
            if r["status"] in ("fail", "timeout"):
                for l in r["tail"]:
                    print("      " + l[:170], flush=True)
    n = {k: sum(r["status"] == k for r in results) for k in ("pass", "skip", "fail", "timeout", "known")}
    part = f" [shard {a.shard}: {len(run)} of {total} test files]" if a.shard else ""
    print(f"\n{n['pass']} passed, {n['skip']} skipped, {n['known']} known red, {n['fail']} FAILED, {n['timeout']} timed out{part}; "
          f"{len(skipped_here)} not run here (ports / Gemma / macOS-only / hardware):")
    for p, why in skipped_here:
        print(f"   - {p.name}: {why}")
    if n["skip"]:
        print("\nskipped for missing local data (each says how to get it):")
        for why in sorted({r["why"] for r in results if r["status"] == "skip"}):
            print("   - " + why)
    return 1 if n["fail"] or n["timeout"] else 0


if __name__ == "__main__":
    sys.exit(main())
