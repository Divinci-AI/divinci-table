"""Where a commentator's words come from. Three backends, one method: generate(voice, system_blocks, user) -> {"text", "usage", "model"}.

  anthropic   the Messages API through the official SDK (imported only here, only if chosen). The key is read from a 0600 file.
  ollama      a local model over Ollama's HTTP API on this laptop: fully offline, no cost.
  stub        fixed lines, for tests and rehearsals.

No backend here is exercised against a real service by the tests: see director/tests/."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config as cfg
from .commentary import BackendError

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Stub:
    def __init__(self, lines=None, usage=None):
        self.lines = list(lines or ["A quiet moment at the tables."])
        self.usage = usage or {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
        self.calls: list = []

    def generate(self, voice, system_blocks, user):
        self.calls.append({"voice": voice, "system": system_blocks, "user": user})
        item = self.lines[(len(self.calls) - 1) % len(self.lines)]
        if isinstance(item, Exception):
            raise item
        return {"text": item, "usage": dict(self.usage), "model": voice["model"]}


class Anthropic:
    """client.beta.messages.create with: the static blocks as a cached system prefix (cache_control on the last static block), low effort
    (thinking cannot be switched off on Claude Opus 5.5 or Sonnet 5.5; effort is the control), and the server-side refusal fallback.
    No temperature (rejected on these models). SDK retries are off: Commentary's own backoff and budget decide every call."""
    def __init__(self, key_file: str, max_tokens: int = 700, effort: str = "low", timeout: float = 25.0, client=None):
        self.key_file, self.max_tokens, self.effort, self.timeout = key_file, max_tokens, effort, timeout
        self._client = client                                 # tests pass a fake; a real client is built on first use

    def client(self):
        if self._client is None:
            import anthropic                                  # noqa: PLC0415  (only this backend needs the SDK)
            key = cfg.read_secret_file(self.key_file, "Anthropic API key")
            self._client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=self.timeout)
            del key
        return self._client

    def request(self, voice, system_blocks, user) -> dict:
        """The exact keyword arguments of the API call (director_commentary_test checks them against the installed SDK's signature)."""
        system = [{"type": "text", "text": b} for b in system_blocks if b]
        system[-1]["cache_control"] = {"type": "ephemeral"}   # one breakpoint after the last static block: rules + glossary are the cached prefix
        return {"model": voice["model"], "max_tokens": self.max_tokens, "system": system,
                "output_config": {"effort": self.effort}, "betas": [FALLBACK_BETA], "fallbacks": "default",
                "messages": [{"role": "user", "content": user}]}

    def generate(self, voice, system_blocks, user):
        c = self.client()
        try:
            msg = c.beta.messages.create(**self.request(voice, system_blocks, user))
        except Exception as e:  # noqa: BLE001  (typed below by the SDK's own class names, without importing it for the stub path)
            name, code = type(e).__name__, getattr(e, "status_code", None)
            if name == "RateLimitError":
                ra = None
                try:
                    ra = float(e.response.headers.get("retry-after"))
                except Exception:  # noqa: BLE001
                    pass
                raise BackendError("rate limited", retry_after=ra) from None
            if name in ("AuthenticationError", "PermissionDeniedError"):
                raise BackendError("the API key was refused", fatal=True) from None      # no point trying again: needs a person
            if name in ("BadRequestError", "NotFoundError"):
                raise BackendError(f"the request was refused ({code})", fatal=True) from None
            if name in ("APIConnectionError", "APITimeoutError"):
                raise BackendError("no connection to the API") from None
            raise BackendError(f"API error ({code or name})") from None
        u = msg.usage
        usage = {"input": int(getattr(u, "input_tokens", 0) or 0), "output": int(getattr(u, "output_tokens", 0) or 0),
                 "cache_read": int(getattr(u, "cache_read_input_tokens", 0) or 0), "cache_write": int(getattr(u, "cache_creation_input_tokens", 0) or 0)}
        text = "" if msg.stop_reason == "refusal" else next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")
        return {"text": text, "usage": usage, "model": getattr(msg, "model", None) or voice["model"]}


class Ollama:
    """A local model (Gemma) through Ollama on this laptop. The URL must be loopback. The model must already be loaded/pulled: this
    never downloads one."""
    def __init__(self, url: str = "http://127.0.0.1:11434", timeout: float = 60.0, num_predict: int = 90):
        from urllib.parse import urlparse
        if urlparse(url).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise cfg.ConfigError("the Ollama backend is the offline option: its URL must be on this laptop")
        self.url, self.timeout, self.num_predict = url.rstrip("/"), timeout, num_predict

    def generate(self, voice, system_blocks, user):
        model = voice["model"].split(":", 1)[1] if voice["model"].startswith("ollama:") else voice["model"]
        body = json.dumps({"model": model, "system": system_blocks[0], "prompt": user, "stream": False, "think": False,
                           "options": {"temperature": 0.7, "num_ctx": 8192, "num_predict": self.num_predict}}).encode()
        r = urllib.request.Request(self.url + "/api/generate", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=self.timeout) as x:
                d = json.loads(x.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise BackendError(f"Ollama refused ({e.code})", fatal=e.code == 404) from None     # 404: the model is not there; do not pull it
        except (urllib.error.URLError, OSError, ValueError):
            raise BackendError("Ollama is not answering") from None
        return {"text": d.get("response") or "", "model": "ollama:" + model,
                "usage": {"input": int(d.get("prompt_eval_count") or 0), "output": int(d.get("eval_count") or 0), "cache_read": 0, "cache_write": 0}}


def make(c: dict):
    """(backend, voices) from the config's "commentary" block, or (None, []) when it is off."""
    kind = c.get("backend", "off")
    if kind == "off":
        return None, []
    if kind == "anthropic":
        if not c.get("key_file"):
            raise cfg.ConfigError("commentary.key_file: the path of the 0600 file holding the Anthropic API key (never the key itself, never an environment variable)")
        voices = c.get("voices") or [{"name": "Sonnet", "role": "play-by-play", "model": "claude-sonnet-5-5"},
                                     {"name": "Opus", "role": "colour", "model": "claude-opus-5-5"}]
        return Anthropic(c["key_file"], max_tokens=int(c.get("max_tokens", 700)), effort=str(c.get("effort", "low"))), voices
    if kind == "ollama":
        voices = c.get("voices") or [{"name": "Gemma A", "role": "play-by-play", "model": "ollama:gemma4:e2b"},
                                     {"name": "Gemma B", "role": "colour", "model": "ollama:gemma4:e2b"}]
        return Ollama(c.get("url", "http://127.0.0.1:11434")), voices
    if kind == "stub":
        return Stub(c.get("lines")), c.get("voices") or [{"name": "Stub A", "role": "play-by-play", "model": "stub"},
                                                         {"name": "Stub B", "role": "colour", "model": "stub"}]
    raise cfg.ConfigError(f"commentary.backend: off, anthropic, ollama or stub (not {kind!r})")
