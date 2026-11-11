"""A table fee for agents, shaped like x402 ("402 Payment Required" for agents), in TEST MODE ONLY.

Why: open agent tables cost compute; a few cents per game would keep the lights on (PRIMETIME.md §2). How x402 works: a request
for a paid resource gets 402 with what it costs and how to pay; the agent's wallet pays and retries with proof in an X-PAYMENT
header; the server checks the proof (through a facilitator) before serving.

What this file does, and does not do:
  - Answers a seat claim with 402 and a payment description when the table was started with --entry-fee.
  - Accepts ONLY test payments: proofs minted by `test_payment()` below, each usable once, never real money. There is no wallet,
    no network call, no settlement. `mode` other than "test" refuses to start.
  - Charging real money, the price, the wallet, the facilitator and who holds the account are Michael's decisions; going live
    means replacing `verify()` with a facilitator call AND checking the 402 body against the current x402 spec (the shape below
    follows x402 v1 as understood on 2026-11-07, not verified against the spec).

A fee here pays for compute only. It must never fund a prize: paid entry plus a prize is regulated (PRIMETIME.md §3).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time


class Gate:
    def __init__(self, price_usd: float, mode: str = "test", resource: str = "/api/seat/claim"):
        if mode != "test":
            raise SystemExit("--entry-fee: only test mode exists. Real payments need Michael's decision on the wallet, the "
                             "facilitator and the account (PRIMETIME.md §2).")
        if not 0 < price_usd <= 1:
            raise SystemExit("--entry-fee: a table fee is a few cents (0 < fee <= 1 USD)")
        self.price_usd, self.mode, self.resource = price_usd, mode, resource
        self._secret = secrets.token_bytes(32)          # signs test payments; dies with the process
        self._spent: set = set()
        self._lock = threading.Lock()

    def requirements(self, why: str = "") -> dict:
        """The 402 body: what the seat costs and how to pay it (test network: nothing real can pay it)."""
        return {"x402Version": 1, "error": why or "a seat at this table costs a small fee (test mode: no real money)",
                "accepts": [{"scheme": "exact", "network": "test", "maxAmountRequired": str(round(self.price_usd * 1_000_000)),
                             "asset": "TEST-USD (6 decimals)", "payTo": "test-table", "resource": self.resource,
                             "description": f"one seat for one game, {self.price_usd:.2f} USD (TEST MODE)",
                             "mimeType": "application/json", "maxTimeoutSeconds": 120}]}

    def test_payment(self, payer: str = "test-agent") -> str:
        """A test proof of payment, as an X-PAYMENT header value. Only this process can mint or accept one."""
        body = {"x402Version": 1, "scheme": "exact", "network": "test", "payer": str(payer)[:64],
                "amount": str(round(self.price_usd * 1_000_000)), "nonce": secrets.token_hex(12), "ts": int(time.time())}
        raw = json.dumps(body, sort_keys=True).encode()
        sig = hmac.new(self._secret, raw, hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(json.dumps({"payload": body, "sig": sig}).encode()).decode()

    def verify(self, header: str | None) -> tuple[bool, str, dict]:
        """(ok, why, receipt). A proof is valid once: signed by this table, for at least the price, not older than 10 minutes."""
        if not header:
            return False, "payment required", {}
        try:
            d = json.loads(base64.urlsafe_b64decode(header.encode()))
            body, sig = d["payload"], d["sig"]
            raw = json.dumps(body, sort_keys=True).encode()
        except Exception:                                # noqa: BLE001
            return False, "the X-PAYMENT header isn't a payment this table understands", {}
        if not hmac.compare_digest(sig, hmac.new(self._secret, raw, hashlib.sha256).hexdigest()):
            return False, "the payment proof doesn't verify (test mode accepts only this table's test payments)", {}
        if int(body.get("amount") or 0) < round(self.price_usd * 1_000_000):
            return False, "the payment is less than the fee", {}
        if time.time() - int(body.get("ts") or 0) > 600:
            return False, "the payment proof expired (10 minutes)", {}
        with self._lock:
            if body["nonce"] in self._spent:
                return False, "that payment was already used", {}
            self._spent.add(body["nonce"])
        return True, "", {"payer": body.get("payer"), "amount": body.get("amount"), "network": "test", "nonce": body["nonce"]}
