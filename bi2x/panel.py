"""Live panel view served locally.

A worker thread talks to the board on the serial port and decodes the stream; the page
shows the panel laid out the way it physically is, both analog volumes, START, the four
BT buttons, the two FX buttons, plus the service inputs and the raw input view.
The page also drives the outputs: the LED strips (SetTapeLedData frames), and the
button lamps, whose intensities travel inside the poll frame (SetOutputs).

Only dependency: pyserial. Nothing is published online, everything runs locally.

  http://127.0.0.1:8740

The serial port is exclusive: close anything else talking to the board first.

Usage:
    python panel.py                    # auto-detects the serial port
    python panel.py --port COM5 --http 9000
"""
import json
import os
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial                                   # noqa: E402
from serial.tools import list_ports             # noqa: E402
import decoder as dec                  # noqa: E402
import encoder as enc                  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
BUTTON_ORDER = ["START", "BT-A", "BT-B", "BT-C", "BT-D", "FX-L", "FX-R"]

ALL_INPUTS = BUTTON_ORDER + ["TEST", "SERVICE", "COIN", "HEADPHONE"]

state = {"connected": False, "error": None, "buttons": {n: False for n in BUTTON_ORDER},
        "volL": 0, "volR": 0, "system": {}, "frames": 0, "crc_ok": 0,
        "rate": 0.0, "channels": [0, 0, 0, 0], "port": None,
        # Same positions on the 0..1023 scale, and revolutions since connection.
        "gradL": 0, "gradR": 0, "turnsL": 0.0, "turnsR": 0.0}
# Per-input tracking: press count, cumulative hold time, last hold time.
tracking = {n: {"presses": 0, "total_ms": 0, "last_ms": 0} for n in ALL_INPUTS}
events = deque(maxlen=400)      # {seq, t, input, active, duration_ms}

# Button lamp state. The lamps ride INSIDE the poll frame (SetOutputs 03 11):
# byte 0 of the 44-byte output field is the master enable, the 7 lamps sit at
# offsets 17..23 in BUTTON_ORDER. The protocol carries one intensity byte 0..255
# per output, but these lamps are plain 12 V LEDs, on or off in practice, so the
# state is a boolean, written as 0xff or 0x00. Until the first lamp command the
# captured polls are replayed unchanged; after it, every poll frame is built here
# and carries the current lamp state.
LAMP_FIELD_LEN = 44
LAMP_FIELD_BASE = 17
lamp_state = {n: False for n in BUTTON_ORDER}
lamps_engaged = False

# The eight addressable LED outputs (CN18 F0..F7) and their installed LED counts,
# read from the board's own SetTapeLedData frames during a LAMP CHECK capture.
STRIP_LEDS = [74, 12, 12, 56, 56, 94, 38, 86]
# Current colour of every LED, as 5-bit (r, g, b). This is the state the page shows
# and the state the outbound frames carry.
led_state = [[(0, 0, 0)] * n for n in STRIP_LEDS]
led_last_frames = []                # hex of the frames built by the last LED command
last_poll_raw = None                # on-wire bytes of the last node-0x03 poll response
outbound = deque()                  # frames waiting for the serial worker to send
out_tag = 0                         # tag counter for the frames we build ourselves
_sequence = 0
_held_since = {}                        # input -> instant of the rising edge
lock = threading.Lock()
stop = threading.Event()
started_at = time.time()
worker_thread = None


def _log_edge(name, active, now):
    """Log an edge and update the tracking counters. Called under the lock."""
    global _sequence
    _sequence += 1
    duration = 0
    if active:
        _held_since[name] = now
        tracking[name]["presses"] += 1
    elif name in _held_since:
        duration = int((now - _held_since.pop(name)) * 1000)
        tracking[name]["total_ms"] += duration
        tracking[name]["last_ms"] = duration
    events.append({"seq": _sequence, "t": round(now - started_at, 3),
                       "input": name, "active": active, "duration_ms": duration})


# USB identity of the board, when the adapter reports one. Ports that match are
# offered first; anything else still works, it just is not pre-selected.
KNOWN_USB_IDS = {(0x1CCF, 0x8050)}


def available_ports():
    """Serial ports, most likely candidate first."""
    found = []
    for info in list_ports.comports():
        likely = (info.vid, info.pid) in KNOWN_USB_IDS
        found.append({"port": info.device,
                      "label": info.description or info.device,
                      "likely": likely})
    found.sort(key=lambda p: (not p["likely"], p["port"]))
    return found


def pick_port(asked):
    """Resolve --port: an explicit name, or `auto` for the first candidate."""
    if asked and asked.lower() != "auto":
        return asked
    found = available_ports()
    for entry in found:
        if entry["likely"]:
            return entry["port"]
    return found[0]["port"] if found else None


def start_worker(port):
    """(Re)start the serial session on `port`, replacing any current one."""
    global worker_thread, last_poll_raw
    stop.set()
    if worker_thread and worker_thread.is_alive():
        worker_thread.join(timeout=2.0)
    stop.clear()
    with lock:
        last_poll_raw = None
        state.update(connected=False, error=None, port=port, frames=0, crc_ok=0,
                     rate=0.0)
    worker_thread = threading.Thread(target=serial_loop, args=(port,), daemon=True)
    worker_thread.start()


# Payload encoding modes, as the flags byte names them (bits 7:5).
MODE_NAMES = {2: "plain", 3: "substitution", 4: "compressed"}


def _is_poll(f):
    """True for a node-0x03, 234-byte poll response."""
    return f["node"] == 0x03 and f["size"] == 234


def capture_poll_raw(frames):
    """On-wire bytes of the newest poll response among `frames` (None if none).

    `parse_frames` now carries each frame's own on-wire slice, so there is nothing
    to locate or recompute here.
    """
    for f in reversed(frames):
        if _is_poll(f):
            return f["raw"]
    return None


def debug_layers(raw):
    """The four teaching layers of one on-wire poll response, JSON-ready.

    Pure function of the raw frame bytes; every field is computed through the
    decoder, so what the page shows is exactly what the decoder sees.
    """
    frames = dec.parse_frames(raw)
    if not frames:
        return None
    f = frames[0]
    payload = f["payload"]
    rs = dec.records(payload)
    if not _is_poll(f) or not rs:
        return None
    record0 = rs[0]
    a = dec.analog(record0)
    # On-wire cut: AA | node | tag | size varint | flags | [substitute] | payload | crc7
    nvar = 2 if f["size"] > 127 else 1              # size varint width (as in decoder)
    cut = [("SOF", 1), ("node", 1), ("tag", 1), ("size", nvar), ("flags", 1)]
    if f["encoding"] == 3:
        cut.append(("substitute", 1))
    cut += [("payload", f["size"]), ("crc7", 1)]
    wire, pos = [], 0
    for name, width in cut:
        wire.append({"field": name, "hex": raw[pos:pos + width].hex()})
        pos += width
    return {
        "wire": wire,
        "node": f["node"], "tag": f["tag"], "size": f["size"],
        "encoding": f["encoding"],
        "mode": MODE_NAMES.get(f["encoding"], "unknown"),
        "obfuscated": f["obfuscated"],
        "crc4_ok": f["crc4_ok"],
        "payload_hex": payload.hex(),
        "prefix_hex": payload[:dec.PREFIX].hex(),
        "block_header_hex": dec.block_header(payload).hex(),
        "records": dec.NREC,
        "digital_hex": record0[:7].hex(),
        "analog_hex": record0[7:].hex(),
        "pressed": dec.pressed(record0),
        "volL": {"raw": a[dec.KNOB_L_CH],
                 "graduation": dec.graduation(a[dec.KNOB_L_CH])},
        "volR": {"raw": a[dec.KNOB_R_CH],
                 "graduation": dec.graduation(a[dec.KNOB_R_CH])},
        "system": dec.system_inputs(payload),
    }


def _median_analog(recs):
    """VOL-L and VOL-R, median-filtered over the 5 newest records; the other two
    (unused) channels pass through from the newest record.

    About 0.5% of records carry a knob reading thousands of counts off the trend of
    its window; the median of the five newest (about 2 ms apart) drops those without
    adding real lag. Wrap-safe: the median is taken on the deltas from record 0, so a
    reading near the 65535 -> 0 seam is not mistaken for a jump.
    """
    n = min(5, len(recs))
    samples = [dec.analog(recs[i]) for i in range(n)]
    out = list(samples[0])
    for k in (dec.KNOB_L_CH, dec.KNOB_R_CH):
        deltas = sorted(dec.knob_delta(samples[i][k], out[k]) for i in range(n))
        out[k] = (out[k] + deltas[n // 2]) & 0xFFFF
    return out


def serial_loop(port):
    global last_poll_raw
    try:
        replay = json.load(open(os.path.join(HERE, "replay.json")))
    except OSError:
        with lock:
            state["error"] = ("replay.json is missing, see the README: it holds the "
                              "startup sequence and the poll requests, and you have to "
                              "generate it from your own cabinet.")
        return
    init = [bytes.fromhex(h) for h in replay["init"]]
    poll = {int(k): bytes.fromhex(v) for k, v in replay["poll"].items()}
    try:
        sp = serial.Serial(port, 115200, timeout=0.004, write_timeout=1.0)
    except serial.SerialException as e:
        with lock:
            state["error"] = f"cannot open {port}: {e}"
        return
    try:
        for t in init:
            if stop.is_set():
                return
            sp.write(t)
            sp.flush()
            time.sleep(0.004)
            sp.read(512)
        time.sleep(0.05)
        sp.reset_input_buffer()
        with lock:
            state["connected"] = True

        buffer = bytearray()
        tag = 0
        n = ok = 0
        # Unwrapped travel, so that a revolution can be counted rather than assumed.
        previous_analog = None
        travel = [0, 0]
        window_start = time.time()
        window_frames = 0
        while not stop.is_set():
            # Replayed poll by default; once a lamp is driven, a poll built here,
            # since the lamp state lives in the poll frame and a replayed poll
            # would reset it on the next cycle.
            with lock:
                payload = lamp_poll_payload() if lamps_engaged else None
            if payload is None:
                request = poll[tag & 0x7F]
            else:
                request = enc.encode_frame(tag & 0x7F, payload)
            sp.write(request)
            sp.flush()
            tag = (tag + 1) & 0xFF
            # Send whatever LED/output frames the page has queued, between polls.
            pending = []
            with lock:
                while outbound:
                    pending.append(outbound.popleft())
            for frame in pending:
                sp.write(frame)
                sp.flush()
            buffer += sp.read(sp.in_waiting or 1)
            data = bytes(buffer)
            frames, consumed = dec.parse_stream(data)
            if consumed:
                del buffer[:consumed]
            raw_poll = capture_poll_raw(frames)
            if raw_poll is not None:
                with lock:
                    last_poll_raw = raw_poll
            for f in frames:
                if not _is_poll(f):
                    continue
                rs = dec.records(f["payload"])
                if not rs:
                    continue
                n += 1
                ok += f["crc4_ok"]
                d = dec.digital(rs[0])
                a = _median_analog(rs)      # median over the 5 newest, drops bad reads
                if previous_analog is not None:
                    for k in (0, 1):
                        travel[k] += dec.knob_delta(a[k], previous_analog[k])
                previous_analog = a
                buttons = {name: not (d >> b) & 1 for name, b in dec.BUTTONS.items()}
                system_state = dec.system_inputs(f["payload"])
                now = time.time()
                with lock:
                    for name in BUTTON_ORDER:
                        if buttons[name] != state["buttons"].get(name):
                            _log_edge(name, buttons[name], now)
                    for name in ("TEST", "SERVICE", "COIN", "HEADPHONE"):
                        if bool(system_state.get(name)) != bool(state["system"].get(name)):
                            _log_edge(name, bool(system_state.get(name)), now)
                    state["buttons"] = buttons
                    state["volL"], state["volR"] = a[0], a[1]
                    state["gradL"] = dec.graduation(a[0])
                    state["gradR"] = dec.graduation(a[1])
                    state["turnsL"] = travel[0] / dec.COUNTS_PER_REVOLUTION
                    state["turnsR"] = travel[1] / dec.COUNTS_PER_REVOLUTION
                    state["channels"] = a
                    state["system"] = system_state
                    state["frames"] = n
                    state["crc_ok"] = ok
                    # Raw view: the 56 input bits (1 = released) and the block header.
                    # Only 7 bits and 2 channels are wired here; the rest is what
                    # you watch to discover anything else.
                    state["bits"] = [(d >> i) & 1 for i in range(56)]
                    state["header"] = list(dec.block_header(f["payload"]))
            now = time.time()
            if now - window_start >= 1.0:
                with lock:
                    state["rate"] = (n - window_frames) / (now - window_start)
                window_start, window_frames = now, n
    except Exception as e:                       # noqa: BLE001
        with lock:
            state["error"] = str(e)
    finally:
        with lock:
            state["connected"] = False
        sp.close()


def _hex_to_rgb(text):
    text = text.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _led_frame(strip, pixels, offset=0):
    """Build a SetTapeLedData frame with the next outbound tag. Call under the lock."""
    global out_tag
    frame = enc.tape_led_frame(out_tag, strip, pixels, offset)
    out_tag = (out_tag + 1) & 0xFF
    return frame


def apply_led(colour, brightness, strip=None):
    """Fill one strip (`strip` given) or every strip (`strip` None) with `colour`,
    scaled by `brightness` (0..100). Updates the shown state, builds the frames, and
    queues them for the board when it is connected. Returns the frame hex strings.
    Call under the lock."""
    global led_last_frames
    r8, g8, b8 = _hex_to_rgb(colour)
    pixel = enc.rgb_to_555(r8, g8, b8, brightness / 100.0)
    targets = range(len(STRIP_LEDS)) if strip is None else [strip]
    frames = []
    for s in targets:
        led_state[s] = [pixel] * STRIP_LEDS[s]
        frames.append(_led_frame(s, led_state[s]))
    if state["connected"]:
        outbound.extend(frames)
    led_last_frames = [f.hex() for f in frames]
    return led_last_frames


def lamp_poll_payload():
    """SetOutputs plaintext for one poll frame: 03 11 | 44-byte field | 03 10.

    The trailing 03 10 asks for the input block, so this frame IS the poll: it
    replaces the replayed one completely. Call under the lock."""
    field = bytearray(LAMP_FIELD_LEN)
    field[0] = 0xFF
    for i, name in enumerate(BUTTON_ORDER):
        field[LAMP_FIELD_BASE + i] = 0xFF if lamp_state[name] else 0x00
    return b"\x03\x11" + bytes(field) + b"\x03\x10"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # The page may be opened from another origin (a local web server, or the file
    # itself); without CORS it could not reach the panel server at all. But `*`
    # would also let any random website read the panel state and switch the
    # serial port, so only local origins (and file://, which sends "null") are
    # echoed back.
    def _cors_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return None
        if origin == "null" or origin.startswith(("http://127.0.0.1",
                                                  "http://localhost")):
            return origin
        return None

    def _send(self, code, body, mime):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route, _, query = self.path.partition("?")
        if route == "/state":
            since = 0
            for part in query.split("&"):
                if part.startswith("since="):
                    try:
                        since = int(part[6:])
                    except ValueError:
                        since = 0
            with lock:
                snapshot = dict(state)
                snapshot["tracking"] = tracking
                snapshot["seq"] = _sequence
                snapshot["events"] = [e for e in events if e["seq"] > since]
                body = json.dumps(snapshot).encode()
            return self._send(200, body, "application/json")
        if route == "/ports":
            body = json.dumps({"ports": available_ports(),
                               "current": state.get("port")}).encode()
            return self._send(200, body, "application/json")
        if route == "/lamp/state":
            with lock:
                body = json.dumps({"lamps": dict(lamp_state),
                                   "engaged": lamps_engaged,
                                   "connected": state["connected"],
                                   "frame": enc.encode_frame(0, lamp_poll_payload()).hex()
                                   }).encode()
            return self._send(200, body, "application/json")
        if route == "/led/state":
            with lock:
                strips = [{"leds": n, "pixels": led_state[s]}
                          for s, n in enumerate(STRIP_LEDS)]
                body = json.dumps({"strips": strips, "connected": state["connected"],
                                   "frames": led_last_frames}).encode()
            return self._send(200, body, "application/json")
        if route == "/debug":
            with lock:
                raw = last_poll_raw
                connected = state["connected"]
            layers = debug_layers(raw) if raw else None
            body = json.dumps({"connected": connected, "layers": layers}).encode()
            return self._send(200, body, "application/json")
        if route == "/reset":
            with lock:
                for v in tracking.values():
                    v.update(presses=0, total_ms=0, last_ms=0)
                events.clear()
            return self._send(200, b'{"ok":true}', "application/json")
        files = {"/": ("panel.html", "text/html; charset=utf-8"),
                    "/panel.css": ("panel.css", "text/css; charset=utf-8"),
                    "/panel.js": ("panel.js", "text/javascript; charset=utf-8")}
        if route in files:
            name, mime = files[route]
            try:
                with open(os.path.join(WEB, name), "rb") as f:
                    return self._send(200, f.read(), mime)
            except OSError:
                return self._send(404, b"not found", "text/plain")
        self._send(404, b"not found", "text/plain")

    def do_OPTIONS(self):
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_POST(self):
        """Commands from the page: port selection, LED bench, button lamps."""
        global lamps_engaged
        route = self.path.split("?")[0]
        if route == "/connect":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                asked = json.loads(self.rfile.read(length) or b"{}").get("port")
            except ValueError:
                asked = None
            if not asked:
                return self._send(400, b'{"error":"no port"}', "application/json")
            start_worker(asked)
            return self._send(200, json.dumps({"port": asked}).encode(),
                              "application/json")
        if route in ("/led/all", "/led/strip", "/led/clear"):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, b'{"error":"json"}', "application/json")
            colour = body.get("colour", "#000000")
            brightness = float(body.get("brightness", 100))
            strip = None
            if route == "/led/strip":
                strip = int(body.get("strip", -1))
                if not (0 <= strip < len(STRIP_LEDS)):
                    return self._send(400, b'{"error":"range"}', "application/json")
            with lock:
                if route == "/led/clear":
                    frames = apply_led("#000000", 100)
                else:
                    frames = apply_led(colour, brightness, strip)
                connected = state["connected"]
            return self._send(200, json.dumps({"ok": True, "connected": connected,
                                               "frames": frames}).encode(),
                              "application/json")
        if route not in ("/lamp/set", "/lamp/all"):
            return self._send(404, b"not found", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, b'{"error":"json"}', "application/json")
        on = bool(body.get("on"))
        with lock:
            if route == "/lamp/set":
                lamp = body.get("lamp")
                if lamp not in lamp_state:
                    return self._send(400, b'{"error":"lamp"}', "application/json")
                lamp_state[lamp] = on
            else:
                for name in lamp_state:
                    lamp_state[name] = on
            # From now on the serial worker builds every poll frame itself.
            lamps_engaged = True
            answer = {"ok": True, "connected": state["connected"],
                      "lamps": dict(lamp_state),
                      "frame": enc.encode_frame(0, lamp_poll_payload()).hex()}
        self._send(200, json.dumps(answer).encode(), "application/json")


def option(name, fallback, cast=str):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def main():
    port = pick_port(option("--port", "auto"))
    http = option("--http", 8740, int)
    if port is None:
        print("[!] no serial port found, plug the board in, or pass --port")
    else:
        start_worker(port)
    server = ThreadingHTTPServer(("127.0.0.1", http), Handler)
    print(f"[*] panel: http://127.0.0.1:{http}   (serial port: {port or 'none'})")
    print("    Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] stopped")
    finally:
        stop.set()
        server.server_close()
        time.sleep(0.2)
        os._exit(0)


if __name__ == "__main__":
    main()
