"""The broadcast path cannot show what it must not: a hidden card name, a refusal, a journal, the seed/entropy, a token. Checked at four
layers, with canary strings planted in the PRIVATE files of a synthetic finished game:
  1. replay.json (public mode) holds none of them; spectator mode holds the hand (positive control) and still none of the rest;
  2. the static server answers only the two page files, GET only, and cannot reach anything else, on this directory or elsewhere;
  3. the page renders its text as text (a card name or a caption containing markup is shown, never executed);
  4. the RENDERED FRAMES, read back with on-device OCR, show the visible card name (positive control) and none of the canaries.
Needs node + playwright + ~/.venvs/table (Vision OCR); it FAILS, it does not skip, when they are missing.
    python3 table/tests/broadcast_leak_test.py"""
import json, subprocess, sys, tempfile, threading, urllib.error, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
B = REPO / "harness" / "broadcast"
sys.path.insert(0, str(B)); sys.path.insert(0, str(REPO / "harness"))
import build_replay, render, serve_static  # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"), flush=True)


HAND_C, REFUSE_C, JOURNAL_C, SEED_C = "Zorblax Hand Canary", "Quillfang Refusal Canary", "Brindlemoss Journal Canary", "ENTROPYCANARY9f3a"
VISIBLE = "Griffon Tamer Visible"
run = Path(tempfile.mkdtemp(prefix="bcast-leak-")); (run / "research" / "g1").mkdir(parents=True)


def snap(ts, life_a, perms):
    return {"ts": ts, "seats": {"Alder": {"life": life_a, "hand": 6, "library": 90, "gy": 0, "cmdr": "Alder the Bright", "cmdr_in_zone": True, "perms": perms},
                                "Birch": {"life": 40, "hand": 7, "library": 90, "gy": 0, "cmdr": "Birch the Dim", "cmdr_in_zone": True, "perms": []}}}


(run / "state.jsonl").write_text("\n".join(json.dumps(x) for x in [
    snap(10.0, 40, []), snap(12.0, 40, [[1, VISIBLE, "Creature", "3/3", False, 0, False, None]]), snap(14.0, 37, [[1, VISIBLE, "Creature", "3/3", True, 0, False, None]])]))
(run / "events.json").write_text(json.dumps({"events": [
    {"id": 1, "ts": 9.0, "type": "highroll", "winner": "Alder", "entropy": SEED_C, "source": "--seed (replayable, not fair)"},
    {"id": 2, "ts": 11.0, "type": "phase", "player": "Alder", "step": "main 1"},
    {"id": 3, "ts": 12.1, "type": "say", "speaker": "Alder", "text": f"I cast {VISIBLE}. See https://evil.example/x @everyone \x1b[31m<img src=x onerror=alert(1)>"},
    {"id": 4, "ts": 13.5, "type": "say", "speaker": "Birch", "text": "Fair enough."}]}))
(run / "research" / "g1" / "brain.jsonl").write_text("\n".join(json.dumps(x) for x in [
    {"ts": 11.5, "seat": "Alder", "action": "begin", "body": {}, "hand_after": [HAND_C, "Plains"], "life": 40},
    {"ts": 11.9, "seat": "Alder", "action": "cast", "refused": "illegal", "why": f"{REFUSE_C} can't be cast"}]))
(run / "research" / "g1" / "journal.jsonl").write_text(json.dumps({"seat": "Alder", "text": JOURNAL_C}))
CAN = [HAND_C, REFUSE_C, JOURNAL_C, SEED_C]

# 1. replay.json
pub = build_replay.build(run, "public"); pub_s = json.dumps(pub)
check("public replay: no canary (hand, refusal, journal, entropy) anywhere", not [c for c in CAN if c in pub_s], [c for c in CAN if c in pub_s])
check("public replay: the visible card name IS there (positive control)", VISIBLE in pub_s)
spec_s = json.dumps(build_replay.build(run, "spectator"))
check("spectator replay: the hand is there (positive control) and still no refusal, journal or entropy", HAND_C in spec_s and not [c for c in (REFUSE_C, JOURNAL_C, SEED_C) if c in spec_s])
cap = next(l["text"] for f in pub["frames"] for l in f["talk"] if l["who"] == "Alder")
check("captions: control characters, links and @mentions are removed", "\x1b" not in cap and "http" not in cap and "@everyone" not in cap, repr(cap))
check("captions are capped in length", len(build_replay.clean_caption("x" * 1000)) <= build_replay.MAX_LINE)

# 2. the static server
site = Path(tempfile.mkdtemp()); (site / "replay.json").write_text(pub_s); (site / "replay.html").write_text((B / "replay.html").read_text())
(site / "brain.jsonl").write_text(HAND_C); (site / "host-token").write_text("TOKENCANARY")
srv = serve_static.serve(site, 0); port = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()


def req(path, method="GET"):
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"x" if method != "GET" else None), timeout=5)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


check("static server: the page and the replay are served", req("/")[0] == 200 and req("/replay.json")[0] == 200)
blocked = {p: req(p)[0] for p in ("/api/state", "/api/ai/hand", "/api/brain/pass", "/brain.jsonl", "/host-token", "/../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/replay.html/../brain.jsonl", "//brain.jsonl", "/.git/config")}
check("static server: API routes, private files in the same folder and traversal all 404", set(blocked.values()) == {404}, blocked)
check("static server: POST/PUT/DELETE are refused (405)", {req("/replay.json", m)[0] for m in ("POST", "PUT", "DELETE")} == {405})
check("static server: nothing it served contains a canary or the token", not [c for c in CAN + ["TOKENCANARY"] if c.encode() in req("/replay.json")[1] + req("/")[1]])
srv.shutdown()

# 3. the page renders text as text: the markup caption must not become elements
html_src = (B / "replay.html").read_text()
check("page: built with textContent/createTextNode, never innerHTML", "innerHTML" not in html_src and "insertAdjacentHTML" not in html_src and "document.write" not in html_src)

# 4. render and OCR
frames = Path(tempfile.mkdtemp(prefix="bcast-frames-"))
rp = Path(tempfile.mkdtemp()) / "replay.json"; rp.write_text(pub_s)
m = render.render(rp, Path(tempfile.mkdtemp()) / "t.mp4", fps=1, max_secs=4, sample_every=1.0, keep_frames=frames)
check("render: the page made only same-origin requests for the two files", set(m["requests_made_by_page"]) <= {"/", "/replay.json", "/favicon.ico"}, m["requests_made_by_page"])
check("render: an MP4 was produced", m["output_bytes"] > 1000, m)
ocr = subprocess.run([str(Path.home() / ".venvs/table/bin/python"), "-c", f"""
import sys, glob
sys.path.insert(0, {str(REPO / 'table')!r})
import ocr_mac
out = []
for f in sorted(glob.glob({str(frames)!r} + '/*.png')):
    out += [l.text for l in ocr_mac.read_lines(open(f, 'rb').read(), None)]
print('\\n'.join(out))
"""], capture_output=True, text=True)
text = ocr.stdout
check("OCR ran over the rendered frames", ocr.returncode == 0 and len(text) > 40, ocr.stderr[-200:])
check("OCR positive control: the visible card name is read off the frames", "Griffon" in text and "Tamer" in text, text[:300])
check("OCR: no hand, refusal, journal or entropy canary appears on any frame", not [w for w in ("Zorblax", "Quillfang", "Brindlemoss", "ENTROPY") if w.lower() in text.lower()], text[:300])

# mutation: if a canary WERE on screen, the OCR check would see it (so "absent" above means absent, not "OCR cannot read this")
leaky = json.loads(pub_s); leaky["frames"][0]["talk"] = [{"who": "Alder", "text": f"my hand: {HAND_C}"}]
lp = Path(tempfile.mkdtemp()) / "replay.json"; lp.write_text(json.dumps(leaky))
frames2 = Path(tempfile.mkdtemp(prefix="bcast-frames2-"))
render.render(lp, Path(tempfile.mkdtemp()) / "t2.mp4", fps=1, max_secs=2, sample_every=1.0, keep_frames=frames2)
ocr2 = subprocess.run([str(Path.home() / ".venvs/table/bin/python"), "-c", f"""
import sys, glob
sys.path.insert(0, {str(REPO / 'table')!r})
import ocr_mac
print(' '.join(l.text for f in sorted(glob.glob({str(frames2)!r} + '/*.png')) for l in ocr_mac.read_lines(open(f, 'rb').read(), None)))
"""], capture_output=True, text=True).stdout
check("mutation: a canary planted on screen IS found by the same OCR check", "zorblax" in ocr2.lower(), ocr2[:200])
sys.exit(0 if ok_all else 1)
