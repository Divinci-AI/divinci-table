"""Serve open-alternative-jev (`so1`) behind TypeSafe's /v1/systemone request/response shape, so
the Commander harness (and later the table app) talks to it exactly as it talks to jev-latest.

  ~/.venvs/so1/bin/python so1_server.py --model Qwen/Qwen3.5-4B --port 8791

so1 is pinned to the commit in so1.pin. Stdlib HTTP only; binds 127.0.0.1.

Mapping (only what the harness sends):
  choice  {criteria: {key: desc}}  -> so1.Choice(instructions, ["key — desc", ...]), answered by index
  score   {criteria: [levels]}     -> so1.Choice over the level texts; "score" = expected 0-based index
  noul                             -> so1.yes_no
A one-option choice is answered without the model (so1 needs ≥2 options).
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

import torch
from so1 import Choice, Decider, yes_no

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="Qwen/Qwen3.5-4B")
ap.add_argument("--port", type=int, default=8791)
ap.add_argument("--mode", default="packed", choices=["packed", "separate"])
args = ap.parse_args()

t0 = time.time()
# so1 tries AutoModelForCausalLM first; on Qwen3.5 (a multimodal hybrid checkpoint) that path
# SEGFAULTS during weight loading on MPS (seen 2026-09-24, torch 2.14 / transformers 5.17), while
# the image-text class loads cleanly. Name the class instead of relying on the fallback order.
from transformers import AutoModelForImageTextToText
decider = Decider.from_pretrained(args.model, backend="hf", dtype=torch.bfloat16, device_map="mps",
                                  model_class=AutoModelForImageTextToText)
print(f"loaded {args.model} in {time.time() - t0:.0f}s on {decider.backend.device}", flush=True)
# The first forward pass on MPS compiles kernels (a 21 s first request was measured 2026-09-24).
# Pay that here, before anyone is waiting on an answer.
t0 = time.time()
decider.decide("warm-up", [Choice("Warm-up question", ["a", "b"]), yes_no("Warm-up?")], mode=args.mode)
print(f"warm-up pass {time.time() - t0:.1f}s", flush=True)
LOCK = Lock()                         # one forward pass at a time on MPS
N_REQ = 0


def answer(body):
    state = body.get("state", "")
    state = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    qids, qs, keys, answers = [], [], [], {}
    for qid, q in (body.get("questions") or {}).items():
        t = q.get("type", "choice")
        instr = str(q.get("instructions", ""))
        if t == "choice":
            crit = q.get("criteria") or {}
            ks = list(crit) if isinstance(crit, dict) else [str(c) for c in crit]
            if len(ks) == 1:
                answers[qid] = {"type": "choice", "choice": ks[0], "probabilities": {ks[0]: 1.0},
                                "confidence": 1.0}
                continue
            opts = [f"{k} — {crit[k]}" if isinstance(crit, dict) and crit[k] else k for k in ks]
            qs.append(Choice(instr, opts))
        elif t == "score":
            ks = [str(c) for c in q.get("criteria") or []]
            qs.append(Choice(instr, ks))
        else:
            ks = ["yes", "no"]
            qs.append(yes_no(instr))
        qids.append((qid, t))
        keys.append(ks)
    if qs:
        with LOCK:
            decisions = decider.decide(state, qs, mode=args.mode)
            # The first run's server grew until the Mac hit 8% free memory (2026-09-24): MPS keeps
            # freed blocks cached. Hand them back after every request and log what the driver holds.
            torch.mps.empty_cache()
            global N_REQ
            N_REQ += 1
            if N_REQ % 25 == 0:
                print(f"req {N_REQ}: mps driver {torch.mps.driver_allocated_memory() / 1e9:.1f} GB", flush=True)
        for (qid, t), ks, d in zip(qids, keys, decisions):
            p = [float(x) for x in d.probabilities]
            if t == "choice":
                answers[qid] = {"type": "choice", "choice": ks[d.index],
                                "probabilities": {k: v for k, v in zip(ks, p)}, "confidence": max(p)}
            elif t == "score":
                answers[qid] = {"type": "score", "score": sum(i * v for i, v in enumerate(p)),
                                "probabilities": {str(i): v for i, v in enumerate(p)}, "confidence": max(p)}
            else:
                answers[qid] = {"type": "noul", "noul": p[0]}
    return {"model": f"so1:{args.model}", "answers": answers}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/systemone":
            self.send_error(404)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            t = time.time()
            out = answer(body)
            out["diagnostics"] = {"timing": {"total_ms": round((time.time() - t) * 1000, 1)}}
            data, code = json.dumps(out).encode(), 200
        except Exception as e:                     # report, never crash the server
            data, code = json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()
