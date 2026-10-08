import serial, time
s = serial.Serial("/dev/cu.usbserial-110", 115200, timeout=1); time.sleep(2); s.reset_input_buffer()
def run(cmd, limit, stop_on_error=False):
    s.write((cmd + "\n").encode()); t0 = time.time(); buf = ""
    while time.time() - t0 < limit:
        buf += s.read(s.in_waiting or 1).decode(errors="replace")
        if any(l.strip() == "ok" for l in buf.splitlines()): break
        if stop_on_error and "Error:Probing" in buf: break
    out = "\n".join(l for l in buf.splitlines() if l.strip() and "busy" not in l)
    print(f"{time.strftime('%H:%M:%S')} >>> {cmd} ({time.time()-t0:.0f}s)\n{out[-3500:]}", flush=True)
    return buf
try:
    run("M114", 5)
    b = run("G29", 1500, True)
    if "Probing Failed" not in b:
        run("M500", 10); run("M420 S1", 5); run("M420", 5)
finally:
    s.close(); print("DONE", flush=True)
