# 12-hour guard for when no print is streaming: every 2 minutes, if sender.py is not running, read the temperatures. If either
# heater has a target set or the printer is hot with no job, turn both off and write an ALERT. Heartbeat every ~10 minutes.
import os, re, subprocess, time, serial
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "idleguard.log")
END = time.time() + 12 * 3600
def log(*a):
    with open(OUT, "a") as f: print(time.strftime("%H:%M:%S"), *a, file=f, flush=True)
def sender_alive(): return subprocess.run(["pgrep", "-f", "sender.py /Users"], capture_output=True).returncode == 0
log("idle guard started; runs until", time.strftime("%H:%M", time.localtime(END)))
n = 0
while time.time() < END:
    time.sleep(120); n += 1
    if sender_alive(): continue
    if not os.path.exists("/dev/cu.usbserial-110"): log("note: printer port not present (printer off or cable out)"); continue
    try:
        s = serial.Serial("/dev/cu.usbserial-110", 115200, timeout=1); time.sleep(2); s.reset_input_buffer()
        s.write(b"M105\n"); time.sleep(1.2); r = s.read(s.in_waiting or 1).decode(errors="replace")
        m = re.search(r"T:([\d.]+) /([\d.]+) B:([\d.]+) /([\d.]+)", r)
        if not m: log("note: no temperature reply:", r.strip()[:80]); s.close(); continue
        t, tt, b, bt = map(float, m.groups())
        if tt > 0 or bt > 0 or t > 230 or b > 85:
            for c in ("M104 S0", "M140 S0", "M107"): s.write((c + "\n").encode()); time.sleep(0.4)
            log(f"ALERT: idle printer was heating (nozzle {t}/{tt}, bed {b}/{bt}); heaters turned off")
        elif n % 5 == 0: log(f"ok idle: nozzle {t}, bed {b}, heaters off")
        s.close()
    except Exception as e: log("note: could not check:", e)
log("idle guard finished its 12 hours")
