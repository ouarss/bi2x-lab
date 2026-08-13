← [README](../README.md)

# bi2x-lab: the serial conversation

This document explains how the tools talk to the board: the poll model, the startup sequence, and
what `replay.json` holds. For the byte format, see [TECHNICAL.md](TECHNICAL.md).

## The board answers with the whole panel at once

The tools do not ask for one input at a time. There is no "state of button A?", then "state of
button B?". The tools send one generic request ("give the input block"), and the board answers with
the whole panel state in one frame.

```
script -> board :  1 request  (25 bytes)      "give the input block"
script <- board :  1 frame    (234 bytes)  =  the WHOLE panel
                                             ├─ the 56 input bits (all buttons)
                                             ├─ the 4 analog channels (the two knobs)
                                             ├─ TEST / SERVICE / COIN, the counter
                                             └─ and all of this, 17 times
```

The answer holds a 228-byte block: a 7-byte header and 17 records. Each record is the full panel
state at one instant. So each answer gives the 17 newest samples (a sliding window, about 36 ms of
history). One request returns all the buttons and both knobs, 17 deep. There is no way to ask for one
input.

## The startup sequence and the poll requests

`replay.json` holds two things:

- `init`: 210 startup frames. The tools send them one time, at the connection. This is the handshake
  with the board.
- `poll`: 128 entries (keys 0 to 127). Each entry is one poll request of 25 bytes.

## Why 128 poll requests, not one per input

The 128 entries are not one per input. They are one per `tag` value. Look at three examples:

```
poll[0] = aa 02 80 30 9c 3e 58 69 ...
poll[1] = aa 02 81 30 93 29 71 52 ...
poll[2] = aa 02 82 30 9b 10 2e 73 ...
             ▲  ▲▲
             │  └── the tag, which rolls: 80, 81, 82, ...
             └───── AA = start of frame
```

Every entry is the same command ("give the input block"). But each frame carries a `tag` counter that
rolls (0x80, 0x81, 0x82, ...). The rest of the bytes change fully from one line to the next, because
the payload is obfuscated from the tag (the keystream starts from the tag). The tool sends the correct
pre-encoded version for each tag:

```python
sp.write(poll[tag & 0x7F])   # send the pre-encoded version for this tag
tag = (tag + 1) & 0xFF        # 0, 1, 2, ..., 127, 0, 1, ... (128 values)
```

The tools now implement the outbound payload compression (`bi2x/encoder.py`), so a poll request can
be built rather than replayed. By default the tools still replay the 128 recorded versions (one per
tag), because they are known good and there is no need to rebuild them; they only build a poll frame
when it must carry an output state (see the button lamps below). `replay.json` still holds the
startup sequence, which is not reversed.

## The outputs (lamps, LEDs) are not in replay.json

`replay.json` has no line for the outputs. To drive the lamps or the LED strips, the tools build
outbound frames themselves with `bi2x/encoder.py`. The LED strips are sent as their own frames; the
button lamps ride inside the poll frame, so once a lamp is driven the panel builds every poll frame
itself instead of replaying it. See [OUTPUTS.md](OUTPUTS.md).

## Summary

| A common guess | The reality |
|---|---|
| One request per button ("state of button A?") | One generic request; the board returns everything. |
| replay.json = one line per input or output | replay.json = one command, in 128 versions (one per tag), plus 210 startup frames. |
| The decode happens on the request | The decode happens on the answer: `decoder.py` expands the 234 bytes into buttons and knobs. |

The intelligent work is in the decode of the answer (`decoder.py`), not in the request, which is only
a replayed ping.
