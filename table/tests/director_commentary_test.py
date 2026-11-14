"""The two AI commentators (director/commentary.py, feed.py, backends.py, speech.py). NO real model is called: the Anthropic backend
gets a fake client that returns a recorded-shape reply, the Ollama backend talks to a stub HTTP server this test starts on a scratch
port, and `say` is a stub runner.

What each block proves:
  A. the feed is built from what the stream may see: a card that is not released is not in the prompt at all; at a table with a person
     the hand is not in the feed even after release (policy "auto"), and is with "released"; rules text is never in the feed.
  B. check_line: an invented card drops the line, a near-miss is repaired, an unmarked set card that is not in this game drops it, rules
     text read aloud drops it, stage directions and length are cut, NOTHING is silence.
  C. the service: the voices alternate and see each other's last line; a stale line is dropped; captions are labelled as AI.
  D. cost controls: usage is priced, the hourly and per-stream budgets pause it BEFORE the next call ("paused (budget)" is visible to the
     page), errors back off exponentially, a refused key stops it, the kill switch (file or flag) stops calls at once.
  E. the Anthropic request: models, a cached static prefix, low effort, the refusal fallback; no temperature, no thinking budget; the
     key is read from a 0600 file and never appears in the request; a refusal is silence. If the SDK is installed, the request's
     keywords are checked against its real signature (no network).
  F. Ollama: loopback only, never pulls a model, a missing model stops it.
  G. speech: a hand-informed line is never spoken while a person is playing; text reaches `say` as an argument after "--".
What it does NOT prove: anything about how a real model behaves (whether it stays grounded, is funny, keeps to length, or marks card
names as asked). The checks in B are what stands between a model and the screen; their catch rate on real output is unmeasured."""
from __future__ import annotations

import inspect
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace as NS

from director_lib import FakeTable, check, done, free_port, keyfile

from director import backends, config as cfg, feed, speech
from director.commentary import PRICES, RULES, BackendError, Budget, Commentary, check_line
from director.server import Director

TMP = Path(tempfile.mkdtemp(prefix="dir-com-"))


class Clock:
    t = 5000.0
    def __call__(self):
        return self.t


def setup(com=None):
    clock = Clock()
    c = cfg.parse({"tables": [
        {"name": "T1", "url": "http://127.0.0.1:8921", "seats": [{"name": "Michael", "kind": "human", "key_file": keyfile(TMP, "Michael", "h" * 30)},
                                                              {"name": "Claude", "kind": "ai", "key_file": keyfile(TMP, "Claude", "a" * 30)}]},
        {"name": "T2", "url": "http://127.0.0.1:8922", "seats": [{"name": "Fusion", "kind": "ai", "key_file": keyfile(TMP, "Fusion", "b" * 30)},
                                                              {"name": "Botty", "kind": "ai", "key_file": keyfile(TMP, "Botty", "b" * 30)}]}],
        "commentary": com or {}})
    f1, f2 = FakeTable(["Michael", "Claude"]), FakeTable(["Fusion", "Botty"], game="g2")
    return c, f1, f2, clock


print("A. the feed")
c, f1, f2, clock = setup()
d = Director(c, clients={"T1": f1, "T2": f2}, clock=clock)
f1.hands["Claude"] = [f1.card("Claude Old Card")]
f2.hands["Fusion"] = [f2.card("Fusion Card", text="Whenever this creature attacks, draw two cards and gain three life.")]
d.poll(); f1.turn_ends(); d.poll()
f1.hands["Claude"] = [f1.card("Claude Old Card"), f1.card("Claude Fresh Card")]
f1.emit("say", speaker="Claude", text="I cast Tricorder.", action="cast"); f1.emit("cast", player="Claude", name="Tricorder", mv=1.0)
f2.emit("say", speaker="Fusion", text="I cast Warp Core.", action="cast"); f2.emit("cast", player="Fusion", name="Warp Core", mv=4.0)
d.poll()
fd = feed.build(d.watches, "auto")
txt = "\n".join(f["text"] for f in fd["facts"])
check("Claude Fresh Card" not in txt and "Claude Fresh Card" not in fd["names"], "a card drawn this turn at a delayed table is not in the feed")
check("I cast Tricorder" not in txt, "nor is this turn's play at that table (the feed is as late as the stream)")
check("Claude Old Card" not in txt, "policy auto: at a table where a person plays, even the RELEASED hand is not given to the commentators")
check("Fusion Card" in txt and "I cast Warp Core" in txt and fd["hand_informed"], "the all-AI table's hand and plays are in the feed (and the feed is marked hand-informed)")
check("draw two cards" not in txt, "rules text is never in the feed")
check({"Fusion Card", "Warp Core"} <= fd["names"], "the cards they may name are the ones the stream can see")
fd2 = feed.build(d.watches, "released")
check("Claude Old Card" in "\n".join(f["text"] for f in fd2["facts"]) and "Claude Fresh Card" not in json.dumps(fd2["facts"]),
      "policy released: the hand the hand-view is already showing may be used; the unreleased card still is not")
fd3 = feed.build(d.watches, "public-only")
check(not fd3["hand_informed"] and "Fusion Card" not in json.dumps(fd3["facts"]), "policy public-only: no hand anywhere")
d.control({"cmd": "hide"})
check("Fusion Card" not in json.dumps(feed.build(d.watches, "released")["facts"]), "hidden by the operator: out of the feed at once")

print("B. check_line")
names = {"Tricorder", "Warp Core", "U.S.S. Cerritos, Following Up"}
universe = names | {"Assault Drone", "Horta", "Rogue Artificial Intelligence"}
ck = lambda s, **k: check_line(s, names, "Claude: I cast Tricorder.", universe, k.get("sh", set()))  # noqa: E731
check(ck("A fine [[Tricorder]] there.")[0] == "A fine Tricorder there.", "a marked card that is in the game is shown, without the marks")
check(ck("Here comes [[Black Lotus]]!")[0] is None, "an invented card drops the line")
check(ck("That [[Warp Cor]] changes things.")[0] == "That Warp Core changes things.", "a near-miss is repaired to the real name")
check(ck("I bet an Assault Drone is coming.")[0] is None, "an unmarked card of the set that is NOT in this game drops the line")
check(ck("Horta would be a problem.")[0] is not None, "(a short one-word name is not policed unmarked: too many are ordinary words; a limit, stated)")
check(ck("*leans in* Big turn (laughs) for Claude.")[0] == "Big turn for Claude.", "stage directions are removed")
check(ck("NOTHING")[0] is None and ck("")[0] is None, "NOTHING and empty are silence")
long = "Claude is building something here. " * 12
out = ck(long)[0]
check(out is not None and len(out) <= 221 and out.endswith("."), "a long line is cut at a sentence end", str(len(out or "")))
sh = {"whenever this creature attacks draw two cards and"}
check(ck("It says whenever this creature attacks draw two cards and gain life.", sh=sh)[0] is None, "eight words of a card's rules text in a row drop the line")
check(ck("Broken [[mark-up here.")[0] is None, "broken mark-up drops the line")

print("C. the service")
c, f1, f2, clock = setup()
d = Director(c, clients={"T1": f1, "T2": f2}, clock=clock, backend=backends.Stub(["[[Warp Core]] hits the table for Fusion.", "And Botty has no answer to Warp Core.", "NOTHING"]),
             voices=[{"name": "Sonnet", "role": "play-by-play", "model": "claude-sonnet-5-5"}, {"name": "Opus", "role": "colour", "model": "claude-opus-5-5"}])
d.poll()
f2.emit("say", speaker="Fusion", text="I cast Warp Core.", action="cast"); f2.emit("cast", player="Fusion", name="Warp Core", mv=4.0)
d.poll()
check(d.step_commentary() == "line", "something happened: the first voice speaks")
p = d.program()
check(p["caption"]["voice"] == "Sonnet" and p["caption"]["ai"] is True and p["caption"]["model"] == "claude-sonnet-5-5" and "[[" not in p["caption"]["text"],
      "the caption is labelled as AI, with the voice and the model")
check(d.step_commentary() == "gap", "not again inside the minimum gap")
clock.t += 15
f2.emit("life", player="Botty", delta=-5, life=15); d.poll()
check(d.step_commentary() == "line" and d.com.lines[-1]["voice"] == "Opus", "the second voice takes the next turn")
st = d.com.backend.calls
check("COLOUR" in st[1]["user"] and "Sonnet: Warp Core hits the table for Fusion." in st[1]["user"], "…and is shown what its partner just said")
check(st[0]["system"][0] == RULES and st[1]["system"][0] == RULES, "the static rules are the same first block for both voices (one cacheable prefix)")
check("Cards you may name:" in st[1]["user"] and "Warp Core" in st[1]["user"], "the prompt lists the cards it may name")
clock.t += 15
check(d.step_commentary() == "nothing new", "nothing new at the tables: no call is made")
n_calls = len(st)
clock.t += 15; f2.emit("life", player="Botty", delta=-1, life=14); d.poll()
check(d.step_commentary().startswith("dropped: chose silence"), "a voice may choose silence")
clock.t += 11
check(d.program()["caption"] is None, "a caption leaves the screen after its time")


class Slow(backends.Stub):
    def generate(self, voice, system_blocks, user):
        clock.t += 40
        return super().generate(voice, system_blocks, user)


d.com.backend = Slow(["Late news about Botty."])
clock.t += 20; f2.emit("life", player="Botty", delta=-2, life=12); d.poll()
check(d.step_commentary() == "dropped: stale" and d.com.dropped[-1][0] == "stale", "a line that took too long to arrive is dropped, not shown late")
d.com.backend = backends.Stub(["Old news."])
clock.t += 20; f2.emit("life", player="Botty", delta=-2, life=10); d.poll()
orig = d.com.backend.generate
def moving(voice, system_blocks, user):
    for i in range(9):
        f2.emit("say", speaker="Fusion", text=f"I play Forest {i}.", action="land")
    d.poll()
    return orig(voice, system_blocks, user)
d.com.backend.generate = moving
check(d.step_commentary() == "dropped: stale", "a line is dropped if the game moved on by many events while it was being written")

print("D. cost controls")
b = Budget(usd_per_hour=0.05, usd_per_stream=0.08, tokens_per_hour=10**9)
u = {"input": 800, "cache_read": 10000, "cache_write": 0, "output": 250}
cost = b.cost("claude-opus-5-5", u)
check(abs(cost - (800 * 4 + 10000 * 0.2 + 250 * 20) / 1e6) < 1e-9, "a call is priced from its reported usage (input, cached read, output)", str(cost))
check(abs(Budget().cost("claude-sonnet-5-5", {"input": 0, "cache_write": 10000, "cache_read": 0, "output": 0}) - 10000 * 2 * 1.25 / 1e6) < 1e-9, "a cache write costs 1.25x input")
check(Budget().cost("some-new-model", u) >= cost, "an unknown model is priced as the dearest known one, never as free")
c, f1, f2, clock = setup()
stub = backends.Stub(["Botty is in trouble."], usage={"input": 900, "cache_read": 10000, "cache_write": 0, "output": 1500})
d = Director(c, clients={"T1": f1, "T2": f2}, clock=clock, backend=stub, voices=[{"name": "Opus", "role": "colour", "model": "claude-opus-5-5"}])
d.com.budget = Budget(usd_per_hour=0.06, usd_per_stream=0.20, tokens_per_hour=10**9)
d.poll()
outs = []
for i in range(4):
    clock.t += 15; f2.emit("life", player="Botty", delta=-1, life=19 - i); d.poll(); outs.append(d.step_commentary())
check(outs[0] == "line" and outs[1] == "line" and outs[2].startswith("paused (budget)") and len(stub.calls) == 2, "over the hourly budget: no further call is made", str(outs))
check(d.program()["commentary"]["state"] == "paused (budget)", "the page is told 'paused (budget)'")
clock.t += 3601; f2.emit("life", player="Botty", delta=-1, life=1); d.poll()
check(d.step_commentary() == "line", "an hour later it resumes")
d.com.budget.usd_per_stream = 0.05
clock.t += 20
check(d.step_commentary().startswith("paused (budget)") and "stream" in d.step_commentary(), "the per-stream budget is a hard stop for the rest of the stream")
tb = Budget(usd_per_hour=99, usd_per_stream=99, tokens_per_hour=1000); tb.record(0, "claude-sonnet-5-5", {"input": 1200})
check(tb.blocked(10) is not None and tb.blocked(3700) is None, "the hourly token budget also pauses it")
c, f1, f2, clock = setup()
err = backends.Stub([BackendError("API error (529)"), BackendError("API error (529)"), BackendError("rate limited", retry_after=42.0), "Back again."])
d = Director(c, clients={"T1": f1, "T2": f2}, clock=clock, backend=err, voices=[{"name": "S", "role": "play-by-play", "model": "claude-sonnet-5-5"}])
d.poll(); f2.emit("life", player="Botty", delta=-5, life=15); d.poll()
o1 = d.step_commentary(); w1 = d.com.backoff_until - clock.t
check(o1.startswith("error") and w1 == 5.0 and d.step_commentary().startswith("waiting") and len(err.calls) == 1, "an error: wait 5 s, and no call while waiting", f"{o1} {w1}")
clock.t += 6; d.step_commentary(); w2 = d.com.backoff_until - clock.t
check(w2 == 10.0, "the next error doubles the wait", str(w2))
clock.t += 11; d.step_commentary()
check(d.com.backoff_until - clock.t == 42.0, "a rate limit waits as long as the API said")
clock.t += 43
check(d.step_commentary() == "line" and d.com.fails == 0, "then it recovers, and the wait is reset")
fatal = backends.Stub([BackendError("the API key was refused", fatal=True), "never"])
d.com.backend = fatal
clock.t += 20; f2.emit("life", player="Botty", delta=-5, life=10); d.poll(); d.step_commentary()
clock.t += 1000
check(d.step_commentary() == "off (kill switch)" and len(fatal.calls) == 1, "a refused key switches commentary off instead of retrying for ever")
c, f1, f2, clock = setup()
kf = TMP / "commentary.off"
stub = backends.Stub(["A line."])
d = Director(c, clients={"T1": f1, "T2": f2}, clock=clock, backend=stub, voices=[{"name": "S", "role": "play-by-play", "model": "stub"}])
d.com.kill_file = kf
d.poll(); f2.emit("life", player="Botty", delta=-5, life=15); d.poll()
kf.write_text("")
check(d.step_commentary() == "off (kill switch)" and not stub.calls, "the kill-switch file stops calls at once")
kf.unlink()
code, _ = d.control({"cmd": "commentary", "on": False})
check(code == 200 and d.step_commentary() == "off (kill switch)" and not stub.calls, "so does the control page's switch")
d.control({"cmd": "commentary", "on": True})
check(d.step_commentary() == "line", "and it can be switched back on")

print("E. the Anthropic request (fake client; no network)")
APIKEY = "sk-ant-TESTONLY-" + "z" * 40
kfile = TMP / "anthropic.key"; kfile.write_text(APIKEY + "\n"); kfile.chmod(0o600)
seen = {}


class FakeMessages:
    def __init__(self, reply):
        self.reply = reply
    def create(self, **kw):
        seen.update(kw=kw)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def reply(text="A [[Warp Core]] for Fusion.", stop="end_turn", model="claude-sonnet-5-5"):
    # the shape of a Messages API reply: content blocks (a thinking block first), usage with cache fields, stop_reason
    return NS(content=[NS(type="thinking", thinking=""), NS(type="text", text=text)], stop_reason=stop, model=model,
              usage=NS(input_tokens=812, output_tokens=240, cache_read_input_tokens=10230, cache_creation_input_tokens=0))


def backend(rep):
    return backends.Anthropic(str(kfile), client=NS(beta=NS(messages=FakeMessages(rep))))


be, voices = backends.make({"backend": "anthropic", "key_file": str(kfile)})
check([v["model"] for v in voices] == ["claude-sonnet-5-5", "claude-opus-5-5"] and [v["role"] for v in voices] == ["play-by-play", "colour"],
      "the default pairing is Sonnet 5.5 (play-by-play) and Opus 5.5 (colour)")
check(set(PRICES) >= {"claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"}, "both models have a price for the budget")
a = backend(reply())
out = a.generate(voices[0], [RULES, "CARD GLOSSARY\nWarp Core | Artifact"], "the feed")
kw = seen["kw"]
check(kw["model"] == "claude-sonnet-5-5" and kw["messages"] == [{"role": "user", "content": "the feed"}], "the request names the voice's model and carries the feed as the user turn")
check([b_["text"] for b_ in kw["system"]] == [RULES, "CARD GLOSSARY\nWarp Core | Artifact"] and "cache_control" not in kw["system"][0]
      and kw["system"][-1]["cache_control"] == {"type": "ephemeral"}, "the static rules and the glossary are the system prefix, cached with one breakpoint at its end")
check(kw["output_config"] == {"effort": "low"} and kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default",
      "low effort, and the server-side refusal fallback")
check(not ({"temperature", "top_p", "top_k", "thinking", "stream"} & set(kw)), "no temperature and no thinking setting (both are rejected or pointless on these models)")
check(APIKEY not in json.dumps(kw), "the API key is not in the request body")
check(out["text"] == "A [[Warp Core]] for Fusion." and out["usage"] == {"input": 812, "output": 240, "cache_read": 10230, "cache_write": 0} and out["model"] == "claude-sonnet-5-5",
      "the reply's text (not its thinking block) and its usage are read")
check(backend(reply(stop="refusal", text="x")).generate(voices[1], [RULES], "f")["text"] == "", "a refusal is silence, never shown")
for name, kwargs, want in (("RateLimitError", {"response": NS(headers={"retry-after": "17"})}, lambda e: e.retry_after == 17.0 and not e.fatal),
                           ("AuthenticationError", {"status_code": 401}, lambda e: e.fatal),
                           ("APIConnectionError", {}, lambda e: not e.fatal and e.retry_after is None),
                           ("InternalServerError", {"status_code": 500}, lambda e: not e.fatal)):
    exc = type(name, (Exception,), {})("boom " + APIKEY)
    for k_, v_ in kwargs.items():
        setattr(exc, k_, v_)
    try:
        backend(exc).generate(voices[0], [RULES], "f"); check(False, f"{name} is raised as a BackendError")
    except BackendError as e:
        check(want(e) and APIKEY not in str(e) and e.__cause__ is None, f"{name}: mapped (retry/fatal as it should be), and the SDK's own message is not passed on", str(e))
lazy = backends.Anthropic(str(TMP / "missing.key"))
try:
    lazy.client(); check(False, "a missing key file is an error")
except Exception as e:  # noqa: BLE001  (ConfigError, or ImportError where the SDK is not installed: either way no call)
    check(APIKEY not in str(e), "a missing key file (or SDK) stops before any call", type(e).__name__)
try:
    backends.make({"backend": "anthropic"}); check(False, "the Anthropic backend needs key_file")
except cfg.ConfigError as e:
    check("key_file" in str(e), "the Anthropic backend needs key_file (a path), not a key and not an environment variable")
src = inspect.getsource(backends)
check("os.environ" not in src and "argv" not in src, "the backends read no environment variable and no command line")
try:
    from anthropic.resources.beta.messages import Messages  # noqa: PLC0415
    params = set(inspect.signature(Messages.create).parameters)
    check(set(kw) <= params, "the request's keywords all exist on the installed SDK's client.beta.messages.create", str(set(kw) - params))
except ImportError:
    print("  ⏭️  the anthropic SDK is not installed for this Python: signature check skipped (run with ~/.venvs/table/bin/python to include it)")

print("F. Ollama (a stub server on a scratch port; no model is loaded)")
got = {}


class OH(BaseHTTPRequestHandler):
    def log_message(self, *a_):
        pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        got.update(path=self.path, body=body)
        if body["model"] == "not-here":
            self.send_response(404); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}"); return
        data = json.dumps({"response": "Fusion swings in.", "prompt_eval_count": 700, "eval_count": 9}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


port = free_port()
osrv = HTTPServer(("127.0.0.1", port), OH)
threading.Thread(target=osrv.serve_forever, daemon=True).start()
ol = backends.Ollama(f"http://127.0.0.1:{port}")
r = ol.generate({"name": "Gemma A", "role": "play-by-play", "model": "ollama:gemma4:e2b"}, [RULES, "glossary"], "the feed")
check(got["path"] == "/api/generate" and got["body"]["model"] == "gemma4:e2b" and got["body"]["stream"] is False and got["body"]["prompt"] == "the feed"
      and got["body"]["system"] == RULES, "one /api/generate call with the rules as system and the feed as prompt (never /api/pull)")
check(r["text"] == "Fusion swings in." and r["usage"]["input"] == 700 and Budget().cost(r["model"], r["usage"]) == 0.0, "its tokens are counted and cost nothing")
try:
    ol.generate({"name": "x", "role": "colour", "model": "ollama:not-here"}, [RULES], "f"); check(False, "a missing model is fatal")
except BackendError as e:
    check(e.fatal, "a model that is not there stops commentary (it is never downloaded)")
try:
    backends.Ollama("http://10.0.0.5:11434"); check(False, "Ollama must be on this laptop")
except cfg.ConfigError:
    check(True, "the offline backend refuses a URL that is not on this laptop")
osrv.shutdown()

print("G. speech")
check(not speech.may_speak({"hand_informed": True}, True) and speech.may_speak({"hand_informed": True}, False) and speech.may_speak({"hand_informed": False}, True),
      "a hand-informed line is never spoken while a person is still playing a game; public lines are")
ran = []
def runner(cmd, **k):
    ran.append(cmd); Path(cmd[cmd.index("-o") + 1]).write_bytes(b"RIFF"); return NS(returncode=0)
sp = speech.Say(str(TMP / "speech"), {"Opus": "Daniel"}, runner=runner)
f = sp.render({"id": 7, "voice": "Opus", "text": "-v Bad --dangerous"})
check(f == "7.wav" and ran[0][-2:] == ["--", "-v Bad --dangerous"] and ran[0][0] == "/usr/bin/say" and "Daniel" in ran[0], "a line reaches `say` as one argument after --, never as options, no shell")
c, f1, f2, clock = setup({"backend": "off"})
check(Director(c, clients={"T1": f1, "T2": f2}, clock=clock).program()["commentary"] == {"state": "off"}, "commentary is off unless configured")
done()
