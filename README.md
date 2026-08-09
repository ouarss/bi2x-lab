# bi2x-lab

Reading the inputs of an arcade rhythm-game control panel (buttons, service switches
and the two 360° volume encoders) straight from the cabinet's I/O board over its USB
serial link.

The board speaks a framed protocol with a per-packet obfuscation layer. This repository
documents that protocol and ships a small, dependency-light decoder for it.

Status:

- Inputs are fully decoded and validated on hardware.
- Outputs (button lamps and the addressable LED strips) are documented but not yet driven.

## Why

This makes the I/O usable anywhere.

## What is decoded

| Signal | Notes |
|---|---|
| START, BT-A…BT-D, FX-L, FX-R | 7 buttons, active low |
| VOL-L / VOL-R | 12-bit absolute encoders, wrapping, expanded to 16 bits |
| TEST / SERVICE / COIN MECH | service panel |
| HEADPHONE | jack detection |
| 56-bit input field, 4 analog channels | only 7 bits and 2 channels are wired on this cabinet |

Validated on 10 502 captured frames against a timestamped state log:
**100.00 % of payloads byte-identical, 100.00 % of button states exact**, header CRC valid
on every frame of four independent captures.

## Protocol summary

```
frame  : AA | node | tag | size (varint) | flags | [substitute] | payload | crc7
flags  : [7:5] payload encoding | [4] obfuscated | [3:0] header CRC4
payload: two command echoes + a 228-byte block
block  : 7-byte header + 17 records of 13 bytes  (sliding window, record 0 newest)
record : [0..6] 56 digital inputs, active low
         [7..12] 4 analog channels, 12 bits big-endian, expanded (v << 4) | (v >> 8)
```

Header CRC4 uses table `00 0D 03 0E 06 0B 05 08 0C 01 0F 02 0A 07 09 04`, payload CRC7
uses `00 09 12 1B 24 2D 36 3F 48 41 5A 53 6C 65 7E 77`.

### Deobfuscation

Set when bit 4 of the flags byte is set. Seeded per packet from the frame tag:

```python
x = tag ^ 0xAA
for b in payload:
    if (~b & 0xAA) & 0xFF == 0:      # every odd bit set
        yield b                       # unchanged, and the generator does NOT advance
        continue
    x = (x * 0x41C64E6D + 0x3039) & 0xFFFFFFFF
    yield b ^ (x & (0x55 if b & 0x80 else 0x7F))
```

Three details matter, and each one alone breaks the whole stream:

1. the mask applies to the **keystream**, not to the result;
2. the generator **only advances on transformed bytes**, so no position-indexed keystream
   can ever match;
3. bytes whose odd bits are all set pass through untouched, which is why an idle payload,
   full of `0xFF`, looks like plaintext and hides the whole mechanism.

In substitution mode the replacement byte is itself passed through the stream, advancing
it, **before** the payload; the comparison then happens on already-deobfuscated bytes.

## Layout

```
bi2x/decoder.py   framing, CRCs, deobfuscation, block and record accessors
bi2x/client.py    console client, prints inputs live
bi2x/panel.py     local web server for the panel view
bi2x/web/         the panel page (HTML/CSS/JS, no build step, no CDN)
```

`decoder.py` has no dependencies and works offline on any captured stream. The live tools
need `pyserial`.

## Running the live debug web interface

```
pip install pyserial
python bi2x/panel.py            # then open http://127.0.0.1:8740
python bi2x/client.py           # console output
```

The serial port is exclusive: close anything else talking to the board first.

### One local file you must produce yourself

Polling needs request frames. Their payload is compressed, and that compression is not
implemented here, so the tools replay a set of previously observed requests read from
`bi2x/replay.json`. That file is **not distributed**: it is a recording of another
program's output. Generate your own from a capture of your cabinet.

`decoder.py` needs none of this: it decodes any stream you already have.


## Connectors

### CN13 : analog inputs (7 pins)

| Pin | Signal | | Pin | Signal |
|---|---|---|---|---|
| 1 | ANALOG0 | | 5 | GND |
| 2 | n.c. | | 6 | n.c. |
| 3 | ANALOG1 | | 7 | 3.3 V |
| 4 | n.c. | | | |

Two analog inputs, no more. Channels 2 and 3 of the protocol reach no connector.

### CN11 : first digital bank (24 pins)

| Pin | Signal | | Pin | Signal |
|---|---|---|---|---|
| 1 | +5 V | | 13 | INPUT_A2 |
| 2 | GND | | 14 | INPUT_A3 |
| 3 | OUTPUT_A0 | | 15 | INPUT_A4 |
| 4 | OUTPUT_A1 | | 16 | INPUT_A5 |
| 5 | OUTPUT_A2 | | 17 | INPUT_A6 |
| 6 | OUTPUT_A3 | | 18 | INPUT_A7 |
| 7 | OUTPUT_A4 | | 19 | INPUT_A8 |
| 8 | OUTPUT_A5 | | 20 | INPUT_A9 |
| 9 | OUTPUT_A6 | | 21 | GND |
| 10 | OUTPUT_A7 | | 22 | GND |
| 11 | INPUT_A0 | | 23 | +12 V |
| 12 | INPUT_A1 | | 24 | +12 V |

The panel harness lands here: eight lamp drivers and the button contacts.

### CN12 : second digital bank (28 pins)

| Pin | Signal | | Pin | Signal |
|---|---|---|---|---|
| 1 | +5 V | | 15 | INPUT_B4 |
| 2 | GND | | 16 | INPUT_B5 |
| 3 | OUTPUT_B0 | | 17 | INPUT_B6 |
| 4 | OUTPUT_B1 | | 18 | INPUT_B7 |
| 5 | OUTPUT_B2 | | 19 | INPUT_B8 |
| 6 | OUTPUT_B3 | | 20 | INPUT_B9 |
| 7 | OUTPUT_B4 | | 21 | INPUT_B10 |
| 8 | OUTPUT_B5 | | 22 | INPUT_B11 |
| 9 | OUTPUT_B6 | | 23 | INPUT_B12 |
| 10 | OUTPUT_B7 | | 24 | INPUT_B13 |
| 11 | INPUT_B0 | | 25 | GND |
| 12 | INPUT_B1 | | 26 | GND |
| 13 | INPUT_B2 | | 27 | +12 V |
| 14 | INPUT_B3 | | 28 | +12 V |

### CN18 : LED data lines (16 pins)

| Pin | Signal | | Pin | Signal |
|---|---|---|---|---|
| 1 | OUTPUT_F0 | | 9 | GND |
| 2 | OUTPUT_F1 | | 10 | GND |
| 3 | OUTPUT_F2 | | 11 | **+5V_IN** |
| 4 | OUTPUT_F3 | | 12 | +5V2 |
| 5 | OUTPUT_F4 | | 13 | +5 V |
| 6 | OUTPUT_F5 | | 14 | +5 V |
| 7 | OUTPUT_F6 | | 15 | +12 V |
| 8 | OUTPUT_F7 | | 16 | +12 V |

Eight data lines for the addressable strips.

**Pin 11 is an input, not an output.** The board does not generate the 5 V the strips
run on: it expects that rail to be supplied here, and distributes it. Leave it
unconnected and the data lines will toggle while every strip stays dark.

### CN8 : coin path (12 pins)

| Pin | Signal | | Pin | Signal |
|---|---|---|---|---|
| 1 | HOPPER_VCC (+5 V) | | 7 | GND |
| 2 | HOPPER_MOTOR_ON+ | | 8 | HOPPER_SENSOR |
| 3 | HOPPER_GND (+5 V) | | 9 | SELECT_ALARM |
| 4 | HOPPER_MOTOR_ON- | | 10 | SELECT_SEIKA |
| 5 | GND | | 11 | DIODE_P4_P6 |
| 6 | SELECT_BLOCKER | | 12 | +12 V |

### COM1 : serial link (4 pins)

| Pin | Signal |
|---|---|
| 1 | TXD |
| 2 | RXD |
| 3 | GND |
| 4 | +12 V |

Twenty-four digital inputs and twenty-four outputs in total. Only a fraction is wired on
any given panel, which is why most of the 56-bit input field never changes.

## Where each signal sits in a frame

| Signal | Location | Idle | Active |
|---|---|---|---|
| START | record bit 6 | 1 | 0 |
| BT-A / BT-B / BT-C / BT-D | record bits 7 / 8 / 9 / 10 | 1 | 0 |
| FX-L / FX-R | record bits 11 / 12 | 1 | 0 |
| TEST | block header byte 2, bit 1 | 0 | 1 |
| SERVICE | block header byte 2, bit 2 | 0 | 1 |
| COIN MECH | block header byte 2, bit 3 | 0 | 1 |
| HEADPHONE | block header byte 4 | 0x00 | non-zero |
| VOL-L / VOL-R | analog channels 0 / 1 | steady, ±16 of jitter | 0..65535, wraps |
| analog ch2, ch3 | analog channels 2, 3 | constant | never move, no connector |
| poll counter | block header byte 0 | increments every cycle | flat means a dead link |
| bit 47 | record byte 5, bit 7 | always 0 | top bit of a 7-bit field, not an input |

## Outputs

Documented, not implemented. The board exposes 24 outputs across three connectors: eight
lamp drivers at 12 V, eight further outputs, and eight data lines that feed the addressable
LED strips. Most strips are WS2812B running at 5 V off the board's 5 V input rail; a few
are 12 V parts. Driving any of them requires emitting frames, which needs the outbound
command format, the next piece of work.

## Known gaps

- Outbound frames are not implemented, so nothing can be written to the board.

## License

MIT. See [LICENSE](LICENSE). Permissive on purpose: the point of this work is that
other people can reuse it.
