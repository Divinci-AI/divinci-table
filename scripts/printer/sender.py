# Stream a G-code file to the CR-6 Max over USB. One "ok" per line. Safety: any Error: from the printer, a stop file, or an
# exception turns both heaters off and lifts the nozzle. Control files: /tmp/printer-pause (hold), /tmp/printer-stop (abort),
# /tmp/printer-cmd (one G-code line to inject before the next line, e.g. "M290 Z-0.05" to babystep the nozzle down).
import serial, time, os, sys, re
GC = sys.argv[1]
PAUSE, STOP, CMD = "/tmp/printer-pause", "/tmp/printer-stop", "/tmp/printer-cmd"
for f in (PAUSE, STOP, CMD):
    if os.path.exists(f): os.remove(f)
lines = []
for raw in open(GC):
    c = raw.split(";")[0].strip()
    if c: lines.append((c, raw.strip()))
s = serial.Serial("/dev/cu.usbserial-110", 115200, timeout=0.5); time.sleep(3); s.reset_input_buffer()
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
def send(cmd, limit=900):
    s.write((cmd + "\n").encode()); t0 = time.time(); buf = ""
    while time.time() - t0 < limit:
        buf += s.read(s.in_waiting or 1).decode(errors="replace")
        for l in buf.splitlines():
            if l.startswith("Error") or "Probing failed" in l or "Probing Failed" in l: raise RuntimeError(l)
        if "ok" in [l.strip().split(" ")[0] for l in buf.splitlines()]: return buf
        if os.path.exists(STOP): raise RuntimeError("stop file")
    raise RuntimeError(f"timeout waiting for ok after: {cmd}")
layer = 0; total = len(lines); t_start = time.time(); last_layer_log = 0
try:
    log(f"sending {total} commands from {GC}")
    for i, (cmd, raw) in enumerate(lines):
        while os.path.exists(PAUSE) and not os.path.exists(STOP): time.sleep(1)
        if os.path.exists(STOP): raise RuntimeError("stop file")
        if os.path.exists(CMD):
            inj = open(CMD).read().strip(); os.remove(CMD)
            if inj: log("INJECT", inj, send(inj, 30).strip().replace("\n", " | ")[:120])
        if cmd.startswith(("M190", "M109", "G28")): log("start of", cmd, "(waits)")
        send(cmd)
        if cmd.startswith("M73"): pass
        if i % 1500 == 0 and i: log(f"progress {100*i//total}%  line {i}/{total}")
    log("FINISHED sending all commands")
except Exception as e:
    log("ABORT:", e)
finally:
    try:
        for c in ("M104 S0", "M140 S0", "G91", "G1 Z10 F300", "G90", "M107"): s.write((c + "\n").encode()); time.sleep(0.3)
        log("heaters off, nozzle lifted")
    except Exception as e: log("cleanup error", e)
    s.close(); print("DONE", flush=True)
