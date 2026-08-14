← [README](../README.md)

# Outputs: the button lamps and the LED strips

This page has the outbound direction (host to board): how a frame drives the button lamps and the
LED strips, and how the panel sends them. For the inbound direction and the frame basics, see
[TECHNICAL.md](TECHNICAL.md). For the serial conversation, see [COMMUNICATION.md](COMMUNICATION.md).

Status: solved and driven. The outbound frame format is reversed, `bi2x/encoder.py` builds the
frames, and the panel drives both the LED strips and the button lamps. What is not yet checked is
the hardware itself (see the last section).

## The outbound frame

Outbound frames are `node 0x02`, the same family as the poll request. The payload is a list of
commands, obfuscated and compressed like the inbound side, but with two differences from the
decoder:

- the obfuscation keystream is seeded on `tag ^ 0x55` (inbound uses `tag ^ 0xAA`);
- the payload checksum (crc7) is computed on the plaintext, then carried THROUGH the keystream, so
  the last wire byte is an encrypted crc7.

The payload compression is a sliding-window LZ (an 85-byte window). `encoder.py` implements it. The
board accepts any valid stream, so the encoder drives the hardware even though it does not reproduce
the vendor compressor byte for byte.

## The LED strips: SetTapeLedData (`03 21`)

    03 21 | strip | 00 | offset (2 bytes, little-endian) | count | count * 2 bytes of pixels

A pixel is a 15-bit 5-5-5 RGB colour, little-endian, bit 15 clear. The channel order is RGB
(confirmed with the operator LAMP CHECK: red is `0x7C21`, green `0x07E1`, blue `0x043F`, white
`0x7FFF`). Note that LAMP CHECK never sends a true black: its OFF is `0x0421`, one count on each
channel.

This cabinet has **8 strips, 428 LEDs**, read from the board's own frames:

| Strip | LEDs | Strip | LEDs |
|---|---|---|---|
| 0 | 74 | 4 | 56 |
| 1 | 12 | 5 | 94 |
| 2 | 12 | 6 | 38 |
| 3 | 56 | 7 | 86 |

## The latch: SetTapeLedLatch (`03 22`)

Writing pixels changes nothing on its own. `03 21` fills a buffer on the board; a separate two-byte
command pushes that buffer onto the strips:

    03 22

It has no argument, and it does not travel in a frame of its own. It rides in the poll frame that
follows the pixel commands, right before the `03 10`:

    03 11 | 44-byte output field | 03 22 | 03 10

Miss it and the strips simply never change, however many pixel commands go out. One cycle looks like
this, and the tag is a single counter shared by every outbound frame:

    tag 93   03 11 … 03 10                    poll
    tag 94   03 21 … 03 21 …                  pixels, several commands per frame
    tag 95   03 21 … 03 21 …                  more pixels
    tag 96   03 11 … 03 22 | 03 10            poll, carrying the latch
    tag 97   03 11 … 03 10                    poll

No frame ever mixes pixel commands with `03 10`, and no payload goes past 263 bytes: a strip that
would not fit is split on the `offset` field. Only the strips that changed are sent.

## The button lamps: SetOutputs (`03 11`)

The lamps ride INSIDE the poll frame, not in a frame of their own:

    03 11 | 44-byte output field | 03 10
    ^^^^^                          ^^^^^
    SetOutputs                     Poll

Each output is one intensity byte (0x00 to 0xFF). Byte 0 of the field is a master output, always
`0xFF`. The 7 button lamps are at offsets 17 to 23, in this order: START, BT-A, BT-B, BT-C, BT-D,
FX-L, FX-R. These lamps are plain 12 V drivers, so in practice they are on (`0xFF`) or off (`0x00`),
even though the byte could carry an intensity.

## The card reader LED: three more bytes of the same field

Offsets 25, 26 and 27 are the red, green and blue of the card reader LED. They are not three
independent lamps: across a whole capture those three bytes only ever hold five combinations, and
they are the operator menu's own palette.

| 25, 26, 27 | Colour |
|---|---|
| `FF FF FF` | white |
| `FF 00 00` | red |
| `00 FF 00` | green |
| `00 00 FF` | blue |
| `00 00 00` | off |

So the reader LED is driven inside the poll frame like the lamps, not by a frame of its own, and it
does take a full 8-bit level per channel: the cabinet breathes it up and down.

## What the game itself sends

The game has its own model of the cabinet, and it is not the one this panel uses. What follows
describes that model. It is useful for two things: driving the strips in a way that looks native,
and knowing what to expect on the wire when the game is the one talking.

One detail matters for the encoder: the game works in 8 bits per channel throughout. The 5-5-5
packing happens at the very end, inside the vendor library, just before the frame goes out.

### The 10 logical zones

The game does not address the 8 strips. It paints one 428-LED span cut into 10 named zones. The cut
lands exactly on the strips this cabinet reports:

| Zone | Name | Offset | LEDs | Strip |
|---|---|---|---|---|
| 0 | TITLE | 0 | 74 | 0 |
| 1 | SPEAKER_L1 | 74 | 12 | 1 |
| 2 | SPEAKER_R1 | 86 | 12 | 2 |
| 3 | WING_L | 98 | 56 | 3 |
| 4 | WING_R | 154 | 56 | 4 |
| 5 | CONPANE | 210 | 94 | 5 |
| 6 | SPEAKER_L2 | 304 | 12 | 6, at offset 0 |
| 7 | SPEAKER_R2 | 316 | 12 | 6, at offset 12 |
| 8 | WOOFER | 328 | 14 | 6, at offset 24 |
| 9 | V_UNIT | 342 | 86 | 7 |

One strip carries three zones: strip 6, 38 LEDs, is the two lower speakers plus the woofer. Every
other zone is a whole strip. So `SetTapeLedData(strip, ...)` maps one to one onto the game's zones,
strip 6 excepted.

The game also groups the zones four ways, for its own brightness control: TITLE with the upper
speakers, the wings with the lower speakers, the woofer with the V-UNIT, and the control panel alone.

### The 9 patterns

One pattern runs at a time, picked by whichever screen is up, and it drives the strips and the card
reader LED together. The only state a pattern keeps is its id and an elapsed-seconds counter, reset
whenever the id changes, so a pattern is a pure function of elapsed time. That is what makes them
replayable outside the game.

| Id | Strips | Where it plays |
|---|---|---|
| 0 | off | scene exits |
| 1 | pulse | never selected by any screen |
| 2 | pulse, frozen at t=0 | boot: the warning screen and the vendor logos |
| 3 | pulse | title screen |
| 4 | rainbow wipe | title screen, second state |
| 5 | pulse | card entry |
| 6 | 2 s fade | song select, station, skill level select |
| 7 | rainbow wipe | song select, other state |
| 8 | 0.25 s fade | in game |

Ids 1, 2, 3 and 5 draw the same thing on the strips; they differ on the other outputs only. Ids 4
and 7 are identical throughout.

There is no separate attract loop. An idle cabinet sits on the title screen, so what you see is
patterns 3 then 4.

The rainbow wipe (4 and 7) is the easiest one to reproduce: the hue advances 0.05 per frame, a full
wheel in 20 frames, while `v = clamp(1 - t, 0, 1)` both dims the colour and pulls back the filled
fraction of each zone over one second.

The pulse (1, 2, 3 and 5) and the two fades (6 and 8) are built from the same handful of drawing
primitives: a 7-colour rainbow laid out one colour per LED, and segments of a zone filled with a
gradient weighted either by `abs(sin(pi/2 * phase))` or linearly, composed onto the zone in one of
six blend modes. The palette behind them is narrow: white and cyan almost everywhere, pink and
purple on the wings and the control panel.

The card reader LED is a separate channel, `SetIccrLed`, not a strip: one packed RGB888 word, not a
pixel array, which reaches the wire as bytes 25 to 27 of the output field. It runs a 240-frame
triangle wave, 4 seconds at 60 Hz, peaking at 255 exactly. The colour depends on the pattern: green
while the reader is free, red while it waits for a card, black once the card is read, and on the
menu and in-game patterns a cyan breath with the blue cut below level 16.

The board takes exactly three outbound commands, and they line up with what this page documents:

| Command | What it drives |
|---|---|
| `SetTapeLedData(strip, data)` | the 8 LED strips |
| `SetIccrLed(rgb)` | the card reader LED, one packed RGB888 word |
| `SetPlayerButtonLamp(index, on)` | the 7 button lamps, index 0 to 6, boolean |

`SetPlayerButtonLamp` takes a boolean and bounds the index to 6, which matches the 7 lamps and the
on/off behaviour seen on the wire. The lamps follow gameplay, not the pattern in progress.

## How the panel drives it

See `bi2x/panel.py` and the LEDs and Panel tabs of the web view.

- **LED strips**: a colour and an intensity, for all strips at once or for one strip. Each change
  queues pixel commands, packed into frames of at most 263 bytes; the next poll carries the latch.
- **Patterns**: the cabinet's own lighting, rebuilt in `bi2x/patterns.py` and played at 60 frames a
  second by a thread of its own. Only the strips that changed are sent, so a still pattern costs
  almost nothing, and a frame is dropped rather than queued if the serial worker falls behind. Card
  entry also drives the reader LED. Picking Custom, or touching a strip by hand, stops the pattern.
- **Button lamps**: an on/off switch for each lamp. A lamp lives in the poll frame, so the server
  replays the captured polls until the first output command, then builds every poll frame itself:
  a replayed poll would reset the lamp state, and it cannot carry the LED latch either.

Everything outbound goes through one queue and one tag counter, held by the serial worker, which is
what the board sees from the game. The board does not seem to require it: the captured polls this
project replays jump the tag five times per 128-frame cycle and are answered all the same. It costs
nothing to be faithful here, so the worker continues from the tag of whatever frame it just sent,
replayed or built.

## Still open (needs the cabinet)

- The frames the panel builds are valid (crc4, crc7, mode 4, obfuscation all check out) and the
  poll payloads are byte-identical to the game's, latch included, but the compressed stream is not:
  our compressor is greedy and picks different matches. Confirm on the cabinet that the board
  answers the built poll frames and that the lamps and strips actually light.
- A byte-exact encoder would need the vendor compressor's hash-chain match finder, which is
  characterised but not ported. It is not needed to drive the hardware.
- The patterns are rebuilt from their description, not checked against a capture of them running:
  the capture this project was reversed from is an operator LAMP CHECK, whose pixels only ever take
  seven test colours. Watching the cabinet is the only way to confirm they look right.
- Encoding modes 0 and 1 are never seen on this node, so they are untested.
- Power: CN18 pin 11 (+5V_IN) must supply the 5 V rail for the strips. If it is not connected, the
  data lines change but every strip stays dark.
