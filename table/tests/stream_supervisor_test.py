"""harness/broadcast/stream_supervisor.py against FAKE platforms on loopback with FAKE keys; nothing leaves this machine.
 - both sinks receive the stream on the right stream key; the keys are in no host process list, no `docker inspect`, no log;
 - killing one sink's container leaves the other streaming and the watchdog brings the dead one back;
 - a frozen sink (a paused container sends nothing) is detected and restarted;
 - the STOP file shows the back-soon card (a different picture size arrives) and ends cleanly with no container left;
 - a sink that cannot connect is given up on after the restart limit, ALERT.txt is written and the exit code is 2.
Needs Docker/OrbStack with the local ffmpeg image. Takes about 3 minutes.   python3 table/tests/stream_supervisor_test.py"""
import json, os, re, subprocess, sys, tempfile, threading, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness" / "broadcast"))
import stream_supervisor as SS  # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"), flush=True)


def wait_for(cond, timeout, step=0.5):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(step)
    return False


T = Path(tempfile.mkdtemp(prefix="stream-sup-")); os.chmod(T, 0o700)
os.umask(0o077)
KEYS = {"a": "FAKEKEY_aaaa1111", "b": "FAKEKEY_bbbb2222"}
PORTS = {"a": 19371, "b": 19372}
for n, k in KEYS.items():
    (T / f"{n}.key").write_text(k); os.chmod(T / f"{n}.key", 0o600)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15", "-t", "12", "-c:v", "libx264",
                "-preset", "ultrafast", "-g", "15", "-pix_fmt", "yuv420p", str(T / "media.mp4")], check=True)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=15", "-t", "6", "-c:v", "libx264",
                "-preset", "ultrafast", "-g", "15", "-pix_fmt", "yuv420p", str(T / "card.mp4")], check=True)

# ── fake platforms: an ffmpeg RTMP listener per sink that only accepts the right stream key, restarted after each connection ──
(T / "recv").mkdir()
stop_listeners = threading.Event()


def listener(name):
    n = 0
    while not stop_listeners.is_set():
        n += 1
        p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-listen", "1", "-i", f"rtmp://127.0.0.1:{PORTS[name]}/live/{KEYS[name]}",
                              "-c", "copy", "-f", "flv", str(T / "recv" / f"{name}_{n:03d}.flv")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while p.poll() is None and not stop_listeners.is_set():
            time.sleep(0.3)
        if p.poll() is None:
            p.terminate()


for n in KEYS:
    threading.Thread(target=listener, args=(n,), daemon=True).start()


def received(name):
    return sum(f.stat().st_size for f in (T / "recv").glob(f"{name}_*.flv"))


def sup_args(extra=(), sinks=None, out=None):
    sinks = sinks or [(n, f"rtmp://host.docker.internal:{PORTS[n]}/live", str(T / f"{n}.key")) for n in KEYS]
    a = [sys.executable, str(REPO / "harness/broadcast/stream_supervisor.py"), "--media", str(T / "media.mp4"), "--card", str(T / "card.mp4"),
         "--out", str(out or T / "out"), "--card-secs", "6", "--frozen-secs", "12", "--check-secs", "3", "--backoff-secs", "2", "--poll-secs", "1"]
    for s in sinks:
        a += ["--sink", *s]
    return a + list(extra)


def containers():
    return [l for l in subprocess.run(["docker", "ps", "-a", "--filter", "name=tbl-stream-", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split() if l]


check("redact() removes every key value", SS.redact("x FAKEKEY_aaaa1111 y FAKEKEY_bbbb2222", list(KEYS.values())) == "x [key] y [key]")
bad = subprocess.run(sup_args(sinks=[("a", "rtmp://x/y;rm -rf", str(T / "a.key"))], out=T / "o_bad"), capture_output=True, text=True)
check("a URL with shell metacharacters is refused", bad.returncode != 0 and "unusual characters" in bad.stderr + bad.stdout)
(T / "loose.key").write_text("x"); os.chmod(T / "loose.key", 0o644)
bad = subprocess.run(sup_args(sinks=[("a", "rtmp://h/live", str(T / "loose.key"))], out=T / "o_bad2"), capture_output=True, text=True)
check("a key file that is group/world readable is refused", bad.returncode != 0 and "0600" in bad.stderr + bad.stdout)

# ── the real run ──
sup_out = open(T / "sup.stdout", "w"); t_start = time.time()
sup = subprocess.Popen(sup_args(), stdout=sup_out, stderr=subprocess.STDOUT, text=True)
try:
    check("both fake platforms receive the stream, each on its own stream key", wait_for(lambda: received("a") > 40000 and received("b") > 40000, 60), (received("a"), received("b")))

    # key hygiene
    ps = subprocess.run(["ps", "-Aeww", "-o", "command="], capture_output=True, text=True).stdout.splitlines()
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)", KEYS["a"]])          # positive control: a process that DOES carry a key must be seen by this very filter
    time.sleep(0.3)
    ps_c = subprocess.run(["ps", "-Aeww", "-o", "command="], capture_output=True, text=True).stdout.splitlines()
    decoy.kill()
    check("positive control: the process-list filter does see a key that is on a command line", [l for l in ps_c if KEYS["a"] in l and "-listen" not in l])
    hits = [l for l in ps if any(k in l for k in KEYS.values()) and "-listen" not in l]
    check("no host process (argv or env) holds either key, other than the fake platforms' own listeners", not hits, [h[:80] for h in hits][:3])
    names = containers()
    insp = " ".join(subprocess.run(["docker", "inspect", n], capture_output=True, text=True).stdout for n in names)
    check("docker inspect (Cmd, Env, Args) holds neither key", names and not [k for k in KEYS.values() if k in insp], names)
    time.sleep(1)
    logtxt = (T / "out" / "supervisor.log").read_text() + (T / "sup.stdout").read_text()
    check("the supervisor's log and stdout hold neither key", not [k for k in KEYS.values() if k in logtxt])
    check("the log file is mode 0600 and the output folder 0700", ((T / "out" / "supervisor.log").stat().st_mode & 0o077) == 0 and ((T / "out").stat().st_mode & 0o077) == 0)

    # watchdog: kill sink a's container; b keeps going; a comes back
    check("healthy sinks are not mistaken for frozen ones over their first 20+ seconds", wait_for(lambda: time.time() - t_start > 25, 40) and "frozen" not in (T / "out" / "supervisor.log").read_text(), (T / "out" / "supervisor.log").read_text()[-300:])
    ca = None
    wait_for(lambda: any("-a-" in n for n in containers()), 20)
    ca = next(n for n in containers() if "-a-" in n)
    b0, a0 = received("b"), received("a")
    subprocess.run(["docker", "kill", ca], capture_output=True)
    check("after killing a's container, b keeps receiving", wait_for(lambda: received("b") > b0 + 20000, 30), (b0, received("b")))
    check("...and the watchdog restarts a, which receives again", wait_for(lambda: received("a") > a0 + 20000 and ca not in containers(), 60), (a0, received("a")))
    check("the restart was logged", "sink a: exited" in (T / "out" / "supervisor.log").read_text() or "sink a: pushing" in (T / "out" / "supervisor.log").read_text())

    # frozen: pause b's container
    cb = next(n for n in containers() if "-b-" in n)
    subprocess.run(["docker", "pause", cb], capture_output=True)
    check("a frozen sink (paused container) is detected and restarted", wait_for(lambda: "sink b: frozen" in (T / "out" / "supervisor.log").read_text(), 60), (T / "out" / "supervisor.log").read_text()[-300:])
    b1 = received("b")
    check("...and b receives again after the restart", wait_for(lambda: received("b") > b1 + 20000, 60), (b1, received("b")))

    # STOP: card, then a clean end
    (T / "out" / "STOP").write_text("")
    rc = None
    try:
        rc = sup.wait(timeout=60)
    except subprocess.TimeoutExpired:
        pass
    check("the STOP file ends the supervisor cleanly (exit 0)", rc == 0, rc)
    time.sleep(1.5)
    widths = []
    for f in sorted((T / "recv").glob("a_*.flv")):
        w = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width", "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout.strip()
        widths.append(w)
    check("the back-soon card (160 px wide, not the 320 px game) reached the platform before the end", "160" in widths and "320" in widths, widths)
    check("no container is left running or lying around", not containers(), containers())
finally:
    if sup.poll() is None:
        sup.terminate()
        try:
            sup.wait(15)
        except subprocess.TimeoutExpired:
            sup.kill()
    stop_listeners.set()
    for n in containers():
        subprocess.run(["docker", "rm", "-f", n], capture_output=True)
    subprocess.run(["pkill", "-f", "rtmp://127.0.0.1:1937"], capture_output=True)

# ── give up and alert ──
t0 = time.time()
p = subprocess.run(sup_args(["--max-restarts-per-hour", "2", "--backoff-secs", "1"], sinks=[("dead", "rtmp://host.docker.internal:19399/live", str(T / "a.key"))], out=T / "out_dead"),
                   capture_output=True, text=True, timeout=120)
check("a sink that cannot connect is given up on after the restart limit: exit 2 and an ALERT.txt", p.returncode == 2 and (T / "out_dead" / "ALERT.txt").exists(), (p.returncode, p.stdout[-300:]))
check("...in well under a minute, and the alert text holds no key", time.time() - t0 < 90 and KEYS["a"] not in (T / "out_dead" / "ALERT.txt").read_text())
check("...and nothing was left running", not containers(), containers())
sys.exit(0 if ok_all else 1)
