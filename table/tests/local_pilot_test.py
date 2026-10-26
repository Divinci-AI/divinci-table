"""local_pilot against a fake loopback router: no Jetson, no model. Run: python3 table/tests/local_pilot_test.py"""
import importlib, json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# a proxy in the environment from the start (urlopen reads it once, on first use): every call must still reach the router directly
os.environ.update(http_proxy="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", no_proxy="", NO_PROXY="")
MODELNAME = [None]
SEEN, REPLY = [], {"content": '{"choice": 2, "say": "Onward."}'}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        SEEN.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        body = json.dumps({"model": MODELNAME[0] or "gpt-oss-20b-MXFP4", "choices": [{"message": {"content": REPLY["content"]}}], "usage": {"prompt_tokens": 50, "completion_tokens": 9}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["JETSON_BASE"] = f"http://127.0.0.1:{srv.server_port}/v1"
os.environ["PILOT_LOG"] = str(Path(tempfile.mkdtemp()) / "log.jsonl")
lp = importlib.import_module("local_pilot")


def must_fail(fn, why, text=""):
    try:
        fn()
    except lp.PilotFailure as e:
        assert text in str(e), f"FAIL: {why}: wrong reason: {e}"
        return
    raise SystemExit(f"FAIL: expected PilotFailure: {why}")


r = lp.decide("STATE...", 3)
assert r == {"choice": 2, "say": "Onward."}, r
req = SEEN[-1]
assert req["model"] == "gpt-oss-20b", "model name must be explicit"
assert req["response_format"]["json_schema"]["schema"]["properties"]["choice"]["enum"] == [1, 2, 3]
assert req["messages"][0]["role"] == "system", "static prefix first"
n = len(SEEN); assert lp.decide("x", 1)["skipped"] and len(SEEN) == n, "one legal option must not call the model"
REPLY["content"] = '{"choice": 9, "say": ""}'; must_fail(lambda: lp.decide("x", 3), "choice outside options", "outside")
REPLY["content"] = "not json"; must_fail(lambda: lp.decide("x", 3), "unparseable")
lp.BASE = "http://example.com/v1"; must_fail(lambda: lp.decide("x", 3), "non-loopback base must be refused", "loopback")
lp.BASE = ""; must_fail(lambda: lp.decide("x", 3), "unset base", "not set")

# proxies in the environment must not carry the game state anywhere: the call still reaches the loopback router directly
lp.BASE = f"http://127.0.0.1:{srv.server_port}/v1"; REPLY["content"] = '{"choice": 1, "say": ""}'
n = len(SEEN); assert lp.decide("x", 3)["choice"] == 1 and len(SEEN) == n + 1, "a proxy variable must be ignored"
# a redirect is a failure, not something to follow
class R(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(307); self.send_header("Location", f"http://127.0.0.1:{srv.server_port}/v1/chat/completions"); self.send_header("Content-Length", "0"); self.end_headers()
    def log_message(self, *a): pass
rsrv = HTTPServer(("127.0.0.1", 0), R); threading.Thread(target=rsrv.serve_forever, daemon=True).start()
lp.BASE = f"http://127.0.0.1:{rsrv.server_port}/v1"; n = len(SEEN)
must_fail(lambda: lp.decide("x", 3), "a redirect must not be followed"); assert len(SEEN) == n, "the redirect target must never be called"
lp.BASE = f"http://127.0.0.1:{srv.server_port}/v1"; MODELNAME[0] = "some-other-model"
must_fail(lambda: lp.decide("x", 3), "a different served model must stop the game", "not 'gpt-oss-20b'"); MODELNAME[0] = None
lp.BASE = ""
log = [json.loads(l) for l in open(os.environ["PILOT_LOG"])]
assert any(x.get("skipped") and x["options"] == 1 for x in log), "a forced move must be logged as skipped"
assert log[0]["choice"] == 2 and log[0]["completion_tokens"] == 9 and any("error" in x for x in log), log
print("local_pilot_test: ok")
