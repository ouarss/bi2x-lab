"""Live panel view served locally.

A worker thread talks to the board on the serial port and decodes the stream; the page
shows the panel laid out the way it physically is, both analog volumes, START, the four
BT buttons, the two FX buttons, plus the service inputs and the raw input view.

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
events = deque(maxlen=400)      # {seq, t, entree, state, duree_ms}
desired_outputs = {}                # channel -> bool (intent only, never emitted)
test_colour = "#35e2f0"
_sequence = 0
_held_since = {}                        # entree -> instant du front montant
lock = threading.Lock()
stop = threading.Event()
started_at = time.time()
worker_thread = None


def _log_edge(name, actif, now):
    """Log an edge and update the tracking counters. Called under the lock."""
    global _sequence
    _sequence += 1
    duration = 0
    if actif:
        _held_since[name] = now
        tracking[name]["presses"] += 1
    elif name in _held_since:
        duration = int((now - _held_since.pop(name)) * 1000)
        tracking[name]["total_ms"] += duration
        tracking[name]["last_ms"] = duration
    events.append({"seq": _sequence, "t": round(now - started_at, 3),
                       "input": name, "active": actif, "duration_ms": duration})


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
    global worker_thread
    stop.set()
    if worker_thread and worker_thread.is_alive():
        worker_thread.join(timeout=2.0)
    stop.clear()
    with lock:
        state.update(connected=False, error=None, port=port, frames=0, crc_ok=0,
                     rate=0.0)
    worker_thread = threading.Thread(target=serial_loop, args=(port,), daemon=True)
    worker_thread.start()


def serial_loop(port):
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
                d = dec.digital(rs[0])
                a = dec.analog(rs[0])
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, mime):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # The page may be opened from another origin; without this it could not
        # reach the panel server at all.
        self.send_header("Access-Control-Allow-Origin", "*")
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
        if route == "/outputs":
            with lock:
                body = json.dumps({"supported": False, "outputs": desired_outputs,
                                    "colour": test_colour}).encode()
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_POST(self):
        """Desired output state.

        Nothing is sent to the board: outbound frames are not implemented yet. The
        intent is stored so the interface is already wired for the day writing works,
        and `supporte: false` says so plainly rather than pretending to drive.
        """
        global test_colour
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
        if route != "/output":
            return self._send(404, b"not found", "text/plain")
        taille = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(taille) or b"{}")
        except ValueError:
            return self._send(400, b'{"error":"json"}', "application/json")
        with lock:
            canal = body.get("channel")
            if canal:
                desired_outputs[canal] = bool(body.get("active"))
            if body.get("colour"):
                test_colour = body["colour"]
            reponse = {"supported": False, "outputs": desired_outputs,
                       "colour": test_colour}
        self._send(200, json.dumps(reponse).encode(), "application/json")


def option(name, defaut, cast=str):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else defaut


def main():
    port = pick_port(option("--port", "auto"))
    http = option("--http", 8740, int)
    if port is None:
        print("[!] no serial port found, plug the board in, or pass --port")
    else:
        start_worker(port)
    server = ThreadingHTTPServer(("127.0.0.1", http), Handler)
    print(f"[*] panel: http://127.0.0.1:{http}   (serial port: {port or "none"})")
    print("    Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] arret")
    finally:
        stop.set()
        server.server_close()
        time.sleep(0.2)
        os._exit(0)


if __name__ == "__main__":
    main()
