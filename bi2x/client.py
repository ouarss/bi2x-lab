"""Standalone console client: reads the buttons and the volume encoders live.

Inbound direction is fully decoded (see decoder.py). Outbound requests are replayed:
the poll command is constant and exists in only 128 forms, indexed by `tag & 0x7F`,
stored in replay.json together with the startup sequence. The board accepts replayed
tags, so reading the inputs does not require implementing the request compression.

The serial port is exclusive: close anything else talking to the board first.

Usage:
    python client.py [--port COM4] [--seconds 30] [--raw]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial                                   # noqa: E402
import decoder as dec                  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BUTTON_ORDER = ["START", "BT-A", "BT-B", "BT-C", "BT-D", "FX-L", "FX-R"]


def option(name, fallback, cast=str):
    if name in sys.argv:
        return cast(sys.argv[sys.argv.index(name) + 1])
    return fallback


def main():
    port = option("--port", "COM4")
    duration = option("--seconds", 30.0, float)
    raw = "--raw" in sys.argv

    try:
        replay = json.load(open(os.path.join(HERE, "replay.json")))
    except OSError:
        print("[!] replay.json is missing. See the README.")
        return 1
    init = [bytes.fromhex(h) for h in replay["init"]]
    poll = {int(k): bytes.fromhex(v) for k, v in replay["poll"].items()}
    print(f"[*] {len(init)} startup frames, {len(poll)} poll request forms")

    try:
        sp = serial.Serial(port, 115200, timeout=0.004, write_timeout=1.0)
    except serial.SerialException as e:
        print(f"[!] cannot open {port}: {e}")
        print("    Close anything else using the port, then try again.")
        return 1

    try:
        print(f"[*] {port} open, running the startup sequence...")
        for i, t in enumerate(init):
            sp.write(t)
            sp.flush()
            time.sleep(0.004)
            sp.read(512)
            if i % 50 == 0:
                print(f"    {i}/{len(init)}")
        time.sleep(0.05)
        sp.reset_input_buffer()
        print("[*] startup sequence sent, polling\n")

        buffer = bytearray()
        state = None
        volL = volR = None
        n = ok = 0
        tag = 0
        deadline = time.time() + duration
        while time.time() < deadline:
            sp.write(poll[tag & 0x7F])
            sp.flush()
            tag = (tag + 1) & 0xFF
            buffer += sp.read(sp.in_waiting or 1)
            frames, consumed = dec.parse_stream(bytes(buffer))
            if consumed:
                del buffer[:consumed]
            for f in frames:
                if f["node"] != 0x03 or f["size"] != 234:
                    continue
                rs = dec.records(f["payload"])
                if not rs:
                    continue
                n += 1
                ok += f["crc4_ok"]
                b = dec.buttons_byte(rs[0])
                a = dec.analog(rs[0])
                current = (b, a[0] >> 8, a[1] >> 8)
                if current != state:
                    state = current
                    volL, volR = a[0], a[1]
                    names = ", ".join(x for i2, x in enumerate(BUTTON_ORDER)
                                     if b >> i2 & 1) or "-"
                    print(f"  buttons={names:32s} VOL-L={volL:5d} VOL-R={volR:5d}")
                    if raw:
                        print(f"    rec0 = {' '.join(f'{c:02X}' for c in rs[0])}")
        print(f"\n[*] {n} responses decoded, header CRC valid on "
              f"{100 * ok // max(n, 1)} %")
        if n == 0:
            print("    No response: the board rejected the replayed requests."
                  "\n    Unplug the USB cable, plug it back in, then try again.")
    except KeyboardInterrupt:
        print("\n[*] interrupted")
    finally:
        sp.close()
        print("[*] port closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
