import serial, time, os
s = serial.Serial("/dev/cu.usbserial-110", 115200, timeout=1); time.sleep(2); s.reset_input_buffer()
def send(c, wait=0.4):
    s.write((c + "\n").encode()); time.sleep(wait); return s.read(s.in_waiting or 1).decode(errors="replace")
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
if os.path.exists("/tmp/printer-off"): os.remove("/tmp/printer-off")
t_end = time.time() + 8 * 60
try:
    send("M104 S170"); log("nozzle to 170 for wiping")
    while time.time() < t_end and not os.path.exists("/tmp/printer-off"):
        r = send("M105", 0.4)
        for l in r.splitlines():
            if "T:" in l: log(l.strip()[:60]); break
        time.sleep(6)
finally:
    send("M104 S0"); log("heater OFF"); s.close(); print("DONE", flush=True)
