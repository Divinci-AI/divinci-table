"""Keep-alive for the tests' calls to a local table server.

urllib.request opens a NEW connection for every request and asks the server to close it. A test that polls (the
phase suite waits out priority windows) makes thousands of them, and every closed connection holds its local port
in TIME_WAIT for 30 s on macOS. The phase suite alone filled all 16,384 ephemeral ports (Errno 49, "Can't assign
requested address"), which then failed whatever suite ran next.

    import keepalive; keepalive.install()

routes urlopen() calls for 127.0.0.1 / localhost through one persistent connection per (host, port, thread). Every
other URL (Scryfall fixtures) still goes through urllib unchanged. Behaviour callers rely on is kept: .status and
.read(), use as a context manager, HTTPError (with a readable body) for 4xx/5xx, redirects followed.
"""
from __future__ import annotations

import http.client
import io
import threading
import urllib.error
import urllib.parse
import urllib.request

LOCAL = {"127.0.0.1", "localhost", "::1"}
_orig_urlopen = urllib.request.urlopen
_tls = threading.local()


class _Resp:
    def __init__(self, url, status, reason, headers, body):
        self.url, self.status, self.reason, self.headers, self._body = url, status, reason, headers, body
        self.code = status

    def read(self, n=-1):
        if n is None or n < 0:
            b, self._body = self._body, b""
            return b
        b, self._body = self._body[:n], self._body[n:]
        return b

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def info(self):
        return self.headers

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _conn(host, port, timeout):
    pool = getattr(_tls, "pool", None)
    if pool is None:
        pool = _tls.pool = {}
    c = pool.get((host, port))
    fresh = c is None
    if fresh:
        c = pool[(host, port)] = http.client.HTTPConnection(host, port, timeout=timeout)
    else:
        c.timeout = timeout
        if c.sock is not None:
            c.sock.settimeout(timeout)
    return c, fresh


def _drop(host, port):
    c = getattr(_tls, "pool", {}).pop((host, port), None)
    if c is not None:
        c.close()


def fetch(method, url, data=None, headers=None, timeout=20):
    """One request over the kept-alive connection → (status, reason, headers, body bytes)."""
    u = urllib.parse.urlsplit(url)
    host, port = u.hostname, u.port or 80
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    h = {k: v for k, v in (headers or {}).items() if k.lower() != "connection"}
    for attempt in (0, 1):
        c, fresh = _conn(host, port, timeout)
        try:
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            body = r.read()                       # read it all, so the connection is ready for the next request
            if r.will_close:
                _drop(host, port)
            return r.status, r.reason, r.headers, body
        except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, http.client.CannotSendRequest):
            _drop(host, port)
            if fresh or attempt:                  # a NEW connection failing is a real failure, not a stale one
                raise urllib.error.URLError("connection to the table server failed")
        except Exception:
            _drop(host, port)
            raise


def urlopen(url, data=None, timeout=20, **kw):
    req = url if isinstance(url, urllib.request.Request) else None
    full = req.full_url if req else url
    if urllib.parse.urlsplit(full).hostname not in LOCAL:
        return _orig_urlopen(url, data, timeout, **kw)
    method = req.get_method() if req else ("POST" if data is not None else "GET")
    body = data if data is not None else (req.data if req else None)
    headers = dict(req.header_items()) if req else {}
    if body is not None and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    for _ in range(6):
        status, reason, hdrs, raw = fetch(method, full, body, headers, timeout)
        if status in (301, 302, 303, 307, 308) and hdrs.get("Location"):
            full = urllib.parse.urljoin(full, hdrs["Location"])
            if status in (301, 302, 303):
                method, body = "GET", None
            continue
        break
    if status >= 400:
        raise urllib.error.HTTPError(full, status, reason, hdrs, io.BytesIO(raw))
    return _Resp(full, status, reason, hdrs, raw)


def install():
    urllib.request.urlopen = urlopen
