#!/usr/bin/env python3
"""Push a finished-game video to one or more RTMP(S) platforms at once, keys out of every host command line and environment.

    python3 harness/broadcast/stream_supervisor.py --media game.mp4 --card backsoon.mp4 --out DIR \\
        --sink yt rtmps://a.rtmps.youtube.com:443/live2 /path/to/yt.key \\
        --sink tw rtmp://live.twitch.tv/app /path/to/tw.key
    touch DIR/STOP        "back soon" card for --card-secs, then a clean end

THIS DOES NOT RUN BY ITSELF AGAINST A REAL PLATFORM. Michael creates the stream keys and launches it (plan S4/S5); the tests here
use fake platforms on loopback and fake keys.

How the key stays private
  Each sink is its own ffmpeg in a short-lived container (OrbStack/Docker; an image already on this machine, never pulled). The key
  file is bind-mounted read-only at /run/key and read INSIDE the container (`-rtmp_playpath "$(cat /run/key)"`), so the host's
  argv/env never hold it: `ps`, `pgrep -fl` and `launchctl` on the Mac cannot show it, and `docker inspect` shows only the literal
  `$(cat /run/key)`. Residual exposure, stated plainly: `docker top` / `docker exec` by the same user can show the container's argv, and
  the file is readable by this user (mode 0600). `ffmpeg -/rtmp_playpath FILE` was tried and does not exist on this build.
  Every log line is redacted against the key values before it is written.

What it watches (plan S3)
  a container that exits -> restart with exponential backoff; one that stops SENDING bytes (docker stats) for --frozen-secs
  -> killed and restarted; more than --max-restarts-per-hour for a sink -> that sink is given up on and ALERT.txt is written (and a
  local macOS notification is tried); every sink given up -> exit 2. STOP file or SIGTERM -> the card, then a clean end.
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_IMAGE = "divinci-local-audio-splitter-ffmpeg:latest"


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[key]")
    return text


def docker(*args, timeout=30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def push_command(media: Path, url: str, keyfile: Path, name: str, image: str, loop=True) -> list[str]:
    """The `docker run` for one sink. The only places the key lives: the 0600 file and, inside the container, ffmpeg's argv."""
    ff = ("exec ffmpeg -hide_banner -loglevel warning -stats -stats_period 5 -re " + ("-stream_loop -1 " if loop else "") +
          "-i /media/in.mp4 -f lavfi -i anullsrc=r=44100:cl=stereo -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 64k "
          "-flvflags no_duration_filesize -rtmp_playpath \"$(cat /run/key)\" -f flv " + _q(url))
    return ["docker", "run", "--rm", "-d", "--name", name, "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", "256m", "--pids-limit", "64", "--tmpfs", "/tmp",
            "-v", f"{media.resolve()}:/media/in.mp4:ro", "-v", f"{keyfile.resolve()}:/run/key:ro",
            "--entrypoint", "sh", image, "-c", ff]


def _q(s: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9:/._\-?=&%]+", s):
        raise SystemExit(f"refusing a URL with unusual characters: {s!r}")
    return "'" + s + "'"


class Sink:
    def __init__(self, name, url, keyfile: Path):
        self.name, self.url, self.keyfile = name, url, keyfile
        self.cname, self.started, self.restarts, self.next_try, self.fails = None, 0.0, [], 0.0, 0
        self.tx, self.tx_changed, self.given_up, self.tx_unknown_logged = None, 0.0, False, False


class Supervisor:
    def __init__(self, a, log=None):
        self.a, self.sinks = a, []
        self.out = Path(a.out); self.out.mkdir(parents=True, exist_ok=True); os.chmod(self.out, 0o700)
        self.stop_file = self.out / "STOP"
        self.secrets: list[str] = []
        for name, url, key in a.sink:
            kf = Path(key)
            if not kf.is_file():
                raise SystemExit(f"key file for {name} is missing")
            if kf.stat().st_mode & 0o077:
                raise SystemExit(f"key file for {name} must be mode 0600 (it is {oct(kf.stat().st_mode & 0o777)})")
            self.secrets.append(kf.read_text().strip())
            self.sinks.append(Sink(name, url, kf))
        self.logf = open(self.out / "supervisor.log", "a"); os.chmod(self.out / "supervisor.log", 0o600)
        self.terminate = False
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "terminate", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "terminate", True))

    def log(self, msg: str):
        line = f"{time.strftime('%H:%M:%S')} {redact(msg, self.secrets)}"
        self.logf.write(line + "\n"); self.logf.flush(); print(line, flush=True)

    # ── one sink ───────────────────────────────────────────────────────────────────────────
    def start(self, s: Sink, media: Path, loop=True):
        s.cname = f"tbl-stream-{s.name}-{os.getpid()}-{int(time.time())}"
        r = subprocess.run(push_command(media, s.url, s.keyfile, s.cname, self.a.image, loop), capture_output=True, text=True)
        if r.returncode != 0:
            self.log(f"sink {s.name}: could not start a container: {redact(r.stderr, self.secrets)[:200]}")
            s.cname = None
            return False
        s.started, s.tx, s.tx_changed = time.time(), None, time.time()
        self.log(f"sink {s.name}: pushing to {s.url}")
        return True

    def kill(self, s: Sink):
        if s.cname:
            docker("rm", "-f", s.cname)
            s.cname = None

    def running(self, s: Sink) -> bool:
        if not s.cname:
            return False
        r = docker("inspect", "-f", "{{.State.Running}}", s.cname)
        return r.returncode == 0 and r.stdout.strip() == "true"

    def tx_bytes(self, s: Sink):
        """Bytes this container has sent so far (`docker stats` NetIO, "rx / tx"). A pusher that is alive keeps sending; a frozen one
        does not. (ffmpeg's own `frame=` progress never reaches `docker logs` without a tty, so it cannot be used.)"""
        r = docker("stats", "--no-stream", "--format", "{{.NetIO}}", s.cname, timeout=20)
        m = re.search(r"/\s*([\d.]+)\s*([kKMGT]?i?B)", r.stdout) if r.returncode == 0 else None
        if not m:
            return None
        mult = {"B": 1, "kB": 1e3, "KB": 1e3, "KiB": 1024, "MB": 1e6, "MiB": 2 ** 20, "GB": 1e9, "GiB": 2 ** 30}.get(m.group(2))
        return int(float(m.group(1)) * mult) if mult else None

    def tick(self, s: Sink):
        now = time.time()
        if s.given_up:
            return
        if s.cname is None or not self.running(s):
            if s.cname is not None:
                tail = redact((docker("logs", "--tail", "3", s.cname).stderr or "").strip().replace("\n", " | "), self.secrets)[:200]
                self.log(f"sink {s.name}: exited ({tail})")
                self.kill(s)
            if now < s.next_try:
                return
            s.restarts = [t for t in s.restarts if now - t < 3600]
            if len(s.restarts) >= self.a.max_restarts_per_hour:
                s.given_up = True
                self.alert(f"sink {s.name}: {len(s.restarts)} restarts in an hour: giving up on it")
                return
            s.restarts.append(now); s.fails += 1
            s.next_try = now + min(60.0, self.a.backoff_secs * 2 ** (s.fails - 1))
            self.start(s, Path(self.a.media))
            return
        if now - s.started > 10 and now - getattr(s, "_checked", 0) >= self.a.check_secs:
            s._checked = now
            tx = self.tx_bytes(s)
            if tx is None:                                # unmeasurable: no claim (a guard that cannot measure must not kill a stream)
                if not s.tx_unknown_logged:
                    s.tx_unknown_logged = True
                    self.log(f"sink {s.name}: cannot read its network counter; frozen-sink detection is off for it")
            elif s.tx is None or tx > s.tx:
                s.tx, s.tx_changed = tx, now
                if now - s.started > 60:
                    s.fails = 0                           # it has been healthy for a minute: forget the backoff
            elif now - s.tx_changed > self.a.frozen_secs:
                self.log(f"sink {s.name}: frozen (nothing sent for {now - s.tx_changed:.0f}s): restarting")
                self.kill(s)

    def alert(self, msg: str):
        self.log("ALERT: " + msg)
        (self.out / "ALERT.txt").write_text(redact(msg, self.secrets) + "\n")
        try:                                              # a local notification only; nothing leaves this machine
            subprocess.run(["osascript", "-e", f'display notification "{msg[:120]}" with title "Divinci Table stream"'], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    # ── the run ────────────────────────────────────────────────────────────────────────────
    def run(self) -> int:
        have = docker("image", "inspect", self.a.image)
        if have.returncode != 0:
            raise SystemExit(f"the image {self.a.image} is not on this machine (the supervisor never pulls one)")
        for s in self.sinks:
            self.start(s, Path(self.a.media))
        try:
            while True:
                if self.terminate or self.stop_file.exists():
                    return self.finish()
                for s in self.sinks:
                    self.tick(s)
                if all(s.given_up for s in self.sinks):
                    self.alert("every sink has been given up on")
                    return 2
                time.sleep(self.a.poll_secs)
        finally:
            for s in self.sinks:
                self.kill(s)

    def finish(self) -> int:
        self.log("stopping: showing the back-soon card, then ending")
        for s in self.sinks:
            self.kill(s)
        if self.a.card and Path(self.a.card).exists():
            for s in self.sinks:
                if not s.given_up:
                    self.start(s, Path(self.a.card), loop=True)
            time.sleep(self.a.card_secs)
        for s in self.sinks:
            self.kill(s)
        self.log("ended cleanly")
        return 0


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--media", required=True); ap.add_argument("--card"); ap.add_argument("--out", required=True)
    ap.add_argument("--sink", nargs=3, action="append", metavar=("NAME", "URL", "KEYFILE"), required=True)
    ap.add_argument("--image", default=DEFAULT_IMAGE); ap.add_argument("--card-secs", type=float, default=10.0)
    ap.add_argument("--frozen-secs", type=float, default=20.0); ap.add_argument("--check-secs", type=float, default=5.0); ap.add_argument("--backoff-secs", type=float, default=3.0)
    ap.add_argument("--max-restarts-per-hour", type=int, default=6); ap.add_argument("--poll-secs", type=float, default=2.0)
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(Supervisor(parse()).run())
