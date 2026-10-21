"""
Passive listener: record everything one module sends back while ANOTHER tool (OBDeleven, VCDS, ...) talks to it.

Never sends anything to the car - only harmless 'get name' queries to the bridge itself as a keepalive.
You see the module's replies, not the other tool's requests, which is usually enough to read the sequence.
Both devices must be on the OBD port at the same time (Y-splitter).
"""

import time

from .bridge import FLAG_SETTINGS, FLAG_GET, SETTING_GAP, frame, open_port, read_frames
from .uds import CLUSTER, describe


def run(port, rxid=CLUSTER[1], log_dir="."):
    log_path = f"{log_dir}/listen_{rxid:03X}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    seen = []
    ser = open_port(port)
    time.sleep(0.3)
    print(f"Listening for replies on 0x{rxid:03X} via {port}. Run the other tool now. Ctrl+C to stop.\n")
    t0 = last_keepalive = time.time()
    buf = b""
    with open(log_path, "w") as log:
        log.write("time, payload hex, meaning\n")
        try:
            while True:
                if time.time() - last_keepalive > 10:
                    ser.write(frame(FLAG_SETTINGS | FLAG_GET | SETTING_GAP, 0, 0, b"\x00\x00"))
                    last_keepalive = time.time()
                buf += ser.read(256)
                frames, buf = read_frames(buf)
                for flags, sender, payload in frames:
                    if flags & FLAG_SETTINGS or sender != rxid:
                        continue
                    line = f"+{time.time() - t0:7.2f}s  {payload.hex(' ').upper():48s}  {describe(payload)}"
                    print(line, flush=True)
                    log.write(line + "\n")
                    log.flush()
                    seen.append(payload)
        except KeyboardInterrupt:
            pass
        finally:
            ser.close()
    summarize(seen)
    print(f"Log: {log_path}")
    return seen


def summarize(seen):
    print("\n--- summary ---")
    if not seen:
        print("Nothing heard. Check both devices are on the bus and the ignition is on.")
        return
    sids = {s[0] for s in seen if s}
    if 0x67 in sids:
        print("Security access (0x67) seen: the other tool logged in before writing.")
    writes = [f"{s[1]:02X}{s[2]:02X}" for s in seen if s and s[0] == 0x6E and len(s) >= 3]
    if writes:
        print("Data id writes accepted, in order:", ", ".join(writes))
    mem = [describe(s) for s in seen if s and s[0] == 0x7D]
    if mem:
        print("Raw memory writes (not coding/adaptation):", "; ".join(mem))
