#!/usr/bin/env python3
"""Render a replay to MP4 headlessly and measure what it cost this machine.

    python3 harness/broadcast/render.py REPLAY_JSON --out out.mp4 [--fps 2] [--max-secs 600] [--metrics m.json]

Serves a COPY of replay.html + replay.json through serve_static.py (no table, no token, no API), steps the page with Playwright,
encodes with ffmpeg (h264_videotoolbox if present, else libx264, 30 fps out), and samples memory pressure, swap-outs and free disk
every 10 s while it works. The metrics are the number that decides whether anything more may run alongside: peak swap-out rate,
lowest free-memory percentage, lowest free disk.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import serve_static  # noqa: E402
import table_loop  # noqa: E402  (its samplers)

# Where a Playwright install might be, nearest first: $PW, then a node_modules next to the repo or the current directory.
PW_CANDIDATES = [str(HERE.parent.parent / "node_modules" / "playwright"), str(Path.cwd() / "node_modules" / "playwright")]


def find_pw() -> str:
    for c in [os.environ.get("PW", "")] + PW_CANDIDATES:
        if c and Path(c).exists():
            return c
    sys.exit("no playwright module found: `npm i playwright` in the repo root, or set PW=/path/to/node_modules/playwright")


def sample_loop(stop, samples, interval):
    last = None
    while not stop.is_set():
        so, now = table_loop.swapouts(), time.time()
        rate = None
        if so is not None and last is not None and now > last[1]:
            rate = (so - last[0]) / (now - last[1]) * 60
        if so is not None:
            last = (so, now)
        samples.append({"t": round(now, 1), "free_pct": table_loop.mem_free_pct(), "swapouts_per_min": rate,
                        "disk_gib": round(table_loop.disk_free_gib(Path(tempfile.gettempdir())), 1)})
        stop.wait(interval)


def encoder() -> list[str]:
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    # A keyframe every 2 s at 30 fps (-g 60) and no B-frames: the platforms ask for it and the pushers copy this stream as it is.
    return ["-c:v", "h264_videotoolbox", "-b:v", "2500k", "-g", "60", "-bf", "0"] if "h264_videotoolbox" in enc else \
           ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "2500k", "-g", "60", "-bf", "0", "-sc_threshold", "0"]


def render(replay: Path, out: Path, fps=2, max_secs=600, sample_every=10.0, keep_frames: Path | None = None) -> dict:
    work = Path(tempfile.mkdtemp(prefix="broadcast-render-")); os.chmod(work, 0o700)
    site = work / "site"; site.mkdir()
    shutil.copy(HERE / "replay.html", site / "replay.html"); shutil.copy(replay, site / "replay.json")
    srv = serve_static.serve(site, 0); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    stop, samples = threading.Event(), []
    threading.Thread(target=sample_loop, args=(stop, samples, sample_every), daemon=True).start()
    t0 = time.time()
    try:
        env = {**os.environ, "PW": find_pw()}
        p = subprocess.run(["node", str(HERE / "render.cjs"), f"http://127.0.0.1:{port}/", str(work / "frames"), str(fps), str(max_secs)],
                           capture_output=True, text=True, env=env, timeout=3600)
        if p.returncode != 0:
            raise SystemExit("render failed: " + (p.stderr or p.stdout)[-300:])
        info = json.loads(p.stdout.strip().splitlines()[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", str(work / "frames" / "frame_%05d.png"),
                        *encoder(), "-pix_fmt", "yuv420p", "-r", "30", "-movflags", "+faststart", str(out)], check=True)
        if keep_frames:
            shutil.copytree(work / "frames", keep_frames, dirs_exist_ok=True)       # for the leak test's OCR
    finally:
        stop.set(); srv.shutdown()
        shutil.rmtree(work, ignore_errors=True)
    fp = [s["free_pct"] for s in samples if s["free_pct"] is not None]
    sr = [s["swapouts_per_min"] for s in samples if s["swapouts_per_min"] is not None]
    return {"frames": info["frames"], "video_seconds": info["duration"], "render_seconds": round(time.time() - t0, 1), "fps": fps,
            "requests_made_by_page": info["requests"], "samples": len(samples), "min_free_memory_pct": min(fp) if fp else None,
            "peak_swapouts_per_min": round(max(sr)) if sr else None, "min_disk_gib": min(s["disk_gib"] for s in samples) if samples else None,
            "output": str(out), "output_bytes": out.stat().st_size}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("replay"); ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=int, default=2); ap.add_argument("--max-secs", type=int, default=600); ap.add_argument("--metrics"); ap.add_argument("--keep-frames")
    a = ap.parse_args()
    m = render(Path(a.replay), Path(a.out), a.fps, a.max_secs, keep_frames=Path(a.keep_frames) if a.keep_frames else None)
    print(json.dumps(m, indent=1))
    if a.metrics:
        Path(a.metrics).write_text(json.dumps(m, indent=1))


if __name__ == "__main__":
    main()
