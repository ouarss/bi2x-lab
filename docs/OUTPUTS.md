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
`0x7FFF`).

This cabinet has **8 strips, 428 LEDs**, read from the board's own frames:

| Strip | LEDs | Strip | LEDs |
|---|---|---|---|
| 0 | 74 | 4 | 56 |
| 1 | 12 | 5 | 94 |
| 2 | 12 | 6 | 38 |
| 3 | 56 | 7 | 86 |

## The button lamps: SetOutputs (`03 11`)

The lamps ride INSIDE the poll frame, not in a frame of their own:

    03 11 | 44-byte output field | 03 10
    ^^^^^                          ^^^^^
    SetOutputs                     Poll

Each output is one intensity byte (0x00 to 0xFF). Byte 0 of the field is a master output, always
`0xFF`. The 7 button lamps are at offsets 17 to 23, in this order: START, BT-A, BT-B, BT-C, BT-D,
FX-L, FX-R. These lamps are plain 12 V drivers, so in practice they are on (`0xFF`) or off (`0x00`),
even though the byte could carry an intensity.

## How the panel drives it

See `bi2x/panel.py` and the LEDs and Panel tabs of the web view.

- **LED strips**: a colour and an intensity, for all strips at once or for one strip. Each change
  sends a `SetTapeLedData` frame.
- **Button lamps**: an on/off switch for each lamp. Because a lamp lives in the poll frame, the
  server replays the captured polls until the first lamp command, then builds every poll frame
  itself so the lamp state is not reset on the next cycle.

## Still open (needs the cabinet)

- The frames the panel builds are valid (crc4, crc7, mode 4, obfuscation all check out), but they
  are not byte-identical to the vendor's. Confirm on the cabinet that the board answers the built
  poll frames and that the lamps and strips actually light.
- A byte-exact encoder would need the vendor compressor's hash-chain match finder, which is
  characterised but not ported. It is not needed to drive the hardware.
- Encoding modes 0 and 1 are never seen on this node, so they are untested.
- Power: CN18 pin 11 (+5V_IN) must supply the 5 V rail for the strips. If it is not connected, the
  data lines change but every strip stays dark.
