"""A pilot brain that asks ONE local-router model for a numbered choice (the Orin llama-server router, over `ssh -L`).

Rules from the `orin` session (2026-10-25): the model name is always passed explicitly (a request must never trigger a
different load); the answer is constrained JSON, `choice` an enum of the legal option numbers plus a short `say`;
no silent fallback to option 1 (a failed call raises PilotFailure and the game is flagged); every decision is logged.
Offline rule: the base URL must be loopback. Stdlib only.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

MODEL = os.environ.get("PILOT_MODEL", "gpt-oss-20b")
BASE = os.environ.get("JETSON_BASE", "")                      # e.g. http://127.0.0.1:18081/v1  (the ssh -L end)
MAX_TOKENS = int(os.environ.get("PILOT_MAX_TOKENS", "700"))   # room for low-effort reasoning plus the JSON
SAY_MAX = 200
LOG = Path(os.environ.get("PILOT_LOG", str(Path(__file__).parent / ".cache" / "research" / "pilot-decisions.jsonl")))

# Static prefix first (rules and format), changing state last, so the router can reuse its prompt cache.
SYSTEM = ("You are playing one seat of a Magic: The Gathering Commander game at a table. Each message lists the STATE and "
          "numbered legal OPTIONS. Reply with JSON only: {\"choice\": <option number>, \"say\": <one short in-character line or empty>}. "
          "You can only pick a listed number. Do not talk about the rules or about being an AI.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):                      # a router that redirects is not the router we tunnelled to
        return None


# No proxies (an HTTP_PROXY in the environment would carry the game state off this machine) and no redirects.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

FAILED: list = []                                             # set on any failed call; the brain loop reads it and exits


class PilotFailure(RuntimeError):
    """The model call failed or its answer was unusable. The game must be flagged, never played on with a default."""


def _check_base() -> str:
    if not BASE:
        raise PilotFailure("JETSON_BASE is not set (expected the loopback end of the ssh -L tunnel)")
    host = urlparse(BASE).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise PilotFailure(f"JETSON_BASE must be loopback (offline table), got host {host!r}")
    return BASE.rstrip("/")


def schema(n: int) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["choice", "say"],
            "properties": {"choice": {"type": "integer", "enum": list(range(1, n + 1))},
                           "say": {"type": "string", "maxLength": SAY_MAX}}}


def _log(rec: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def decide(user_prompt: str, n_options: int, game: str = "", timeout: float = 90.0) -> dict:
    """Return {"choice": int, "say": str}. With 0 or 1 option no model is called."""
    if n_options <= 1:
        _log({"ts": round(time.time(), 2), "game": game, "model": MODEL, "options": n_options, "skipped": True})   # a forced move: no call
        return {"choice": 1, "say": "", "skipped": True}
    base = _check_base()
    body = {"model": MODEL, "temperature": 0.7, "max_tokens": MAX_TOKENS, "stream": False,
            "reasoning_effort": "low", "chat_template_kwargs": {"reasoning_effort": "low"},
            "response_format": {"type": "json_schema", "json_schema": {"name": "move", "strict": True, "schema": schema(n_options)}},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_prompt}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    rec = {"ts": round(t0, 2), "game": game, "model": MODEL, "options": n_options}
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        served = str(data.get("model") or "")
        if served and MODEL.lower() not in served.lower():      # the router answered with a different model: stop, never play on
            raise ValueError(f"the router served {served!r}, not {MODEL!r}")
        content = data["choices"][0]["message"].get("content") or ""
        out = json.loads(content)
        choice = int(out["choice"])
        if not 1 <= choice <= n_options:
            raise ValueError(f"choice {choice} outside 1..{n_options}")
        say = str(out.get("say") or "").strip()[:SAY_MAX]
        u = data.get("usage") or {}
        _log({**rec, "latency_s": round(time.time() - t0, 2), "choice": choice, "say_len": len(say), "served": served or None,
              "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
              "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens")})
        return {"choice": choice, "say": say}
    except Exception as e:                                    # noqa: BLE001 — every failure is recorded and raised, never defaulted
        _log({**rec, "latency_s": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {str(e)[:200]}"})
        FAILED.append(f"{MODEL} call failed: {type(e).__name__}: {str(e)[:200]}")
        raise PilotFailure(FAILED[-1]) from e
