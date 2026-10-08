# Dead-man switch for the print. Independent of sender.py. If the sender dies without turning the heaters off, or its log goes
# quiet for STALL seconds, it kills the sender, turns both heaters off, lifts the nozzle, and writes an ALERT line.
import os, sys, time, subprocess, serial
LOG, STALL = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 600
OUT = os.path.join(os.path.dirname(LOG), "watchdog.log")
def log(*a):
    with open(OUT, "a") as f: print(time.strftime("%H:%M:%S"), *a, file=f, flush=True)
def sender_alive(): return subprocess.run(["pgrep", "-f", "sender.py /Users"], capture_output=True).returncode == 0
def emergency(why):
    log("ALERT:", why)
    subprocess.run(["pkill", "-9", "-f", "sender.py /Users"]); time.sleep(1.5)
    try:
        s = serial.Serial("/dev/cu.usbserial-110", 115200, timeout=1); time.sleep(2)
        for c in ("M104 S0", "M140 S0", "G91", "G1 Z10 F300", "G90", "M107"): s.write((c + "\n").encode()); time.sleep(0.4)
        s.close(); log("ALERT: heaters off and nozzle lifted")
    except Exception as e: log("ALERT: could not reach the printer to turn it off:", e)
log("watchdog started on", LOG, "stall limit", STALL, "s")
while True:
    time.sleep(20)
    txt = open(LOG).read() if os.path.exists(LOG) else ""
    done = "heaters off, nozzle lifted" in txt or "DONE" in txt
    if done: log("print finished or aborted cleanly; watchdog exits"); break
    if not sender_alive(): emergency("sender process is gone before the heaters were turned off"); break
    if time.time() - os.path.getmtime(LOG) > STALL: emergency(f"no progress for {STALL} s"); break
