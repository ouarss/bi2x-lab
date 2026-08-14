← [README](../README.md)

# Make your own replay.json

The live tools need `bi2x/replay.json` to poll the board. This project does not ship that file: it is
a recording of another program's serial output. This page shows how to make your own from your
cabinet. You do this one time.

For the reason the tools replay requests, and for the file format, see
[COMMUNICATION.md](COMMUNICATION.md). The short version: `replay.json` holds the startup frames
(`init`) and the 128 poll requests (`poll`, one per `tag` value).

## What you need

- The cabinet, powered, with the I/O board on a serial port.
- The vendor software that drives the board (the real game or its I/O service). Only this software
  can produce valid request frames.
- A way to record the serial traffic in the host-to-board direction. Use one of the two methods
  below.

Keep the capture private, like `replay.json`. It is vendor traffic.

## Method A: a serial proxy (recommended)

Put a proxy between the vendor software and the board. The proxy passes every byte through, and it
writes a copy to a file.

1. Make a virtual serial pair on Windows (for example with com0com): `COM20` and `COM21`.
2. Point the vendor software at `COM20`.
3. Run a small proxy that joins `COM21` to the real board port (for example `COM4`), and logs both
   directions to a file.
4. Start the vendor software. Let it connect and poll for a few seconds.

The host-to-board direction (vendor software to board) has the frames you need.

## Method B: a passive tap

Read the host-to-board line with a second serial adapter, in parallel, at 115200 8N1. This adapter
only listens. It records the frames that the vendor software sends to the board. This needs no
virtual ports, but it needs the extra adapter and the wiring.

## What to record

- The connection. The vendor software sends a startup sequence one time, before it polls. These
  frames are the `init` list.
- A few seconds of steady polling. The poll request repeats with a `tag` that rolls through 128
  values. You must record long enough to see all 128.

To capture the outputs (the lamps and the LED strips), record the host-to-board direction while the
operator test menu drives them. The `LAMP CHECK` screen in `AUTO` mode lights one output at a time,
in a named order, which labels each outbound frame for you. See
[TEST-MENU.md](TEST-MENU.md) for the screens and the sequence.

That capture is enough to read the output format, but not to check colours against anything: LAMP
CHECK only ever sends its own handful of test colours. A capture of the game running would be a
different recording, and this project does not have one.

## Turn the capture into replay.json

Save the host-to-board bytes to a file, for example `host-to-board.bin`. Then run this from the repo
root. It splits the raw frames, keeps the poll requests by `tag`, and writes `bi2x/replay.json`.

```python
import json, sys
sys.path.insert(0, "bi2x")
import decoder as dec

raw = open("host-to-board.bin", "rb").read()

frames, i = [], 0
while True:
    j = raw.find(b"\xAA", i)
    if j < 0:
        break
    parsed = dec.parse_frames(raw[j:j + 512])
    if not parsed:
        i = j + 1
        continue
    f = parsed[0]
    length = 3 + (2 if f["size"] > 127 else 1) + 1 + (1 if f["encoding"] == 3 else 0) + f["size"] + 1
    frames.append((f, raw[j:j + length]))
    i = j + length

# The poll request is the short repeating frame. It has node 0x02 and 25 bytes.
# Everything before the steady poll is the startup sequence.
poll, init, seen_poll = {}, [], False
for f, rawframe in frames:
    is_poll = f["node"] == 0x02 and len(rawframe) == 25
    if is_poll:
        seen_poll = True
        poll[str(f["tag"] & 0x7F)] = rawframe.hex()
    elif not seen_poll:
        init.append(rawframe.hex())

json.dump({"init": init, "poll": poll}, open("bi2x/replay.json", "w"), indent=0)
print("poll variants: %d / 128   init frames: %d" % (len(poll), len(init)))
```

Check the count. `poll variants` must be 128. If it is less, record a longer capture and run the
script again.

The node and size that mark the poll request come from this cabinet. If your capture gives a
different count, print `f["node"]` and `len(rawframe)` for the frames, and adjust the two values in
the `is_poll` test.

## Check that it works

```
python bi2x/client.py
```

The client polls the board and prints the inputs. It also prints the header CRC rate. If the rate is
100%, the `replay.json` is correct. Press the buttons and turn the knobs. The values must change.
