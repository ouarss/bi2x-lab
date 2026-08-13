← [README](../README.md)

# bi2x-lab: protocol and hardware

This document has the protocol, the connectors and the signal map. For the tools and the run steps,
see the [README](../README.md). For the poll model and `replay.json`, see
[COMMUNICATION.md](COMMUNICATION.md).

## Protocol summary

```
frame  : AA | node | tag | size (varint) | flags | [substitute] | payload | crc7
flags  : [7:5] payload encoding | [4] obfuscated | [3:0] header CRC4
payload: two command echoes + a 228-byte block
block  : 7-byte header + 17 records of 13 bytes  (sliding window, record 0 newest)
record : [0..6] 56 digital inputs, active low
         [7..12] 4 analog channels, 12 bits big-endian, expanded (v << 4) | (v >> 8)
```

Header CRC4 uses table `00 0D 03 0E 06 0B 05 08 0C 01 0F 02 0A 07 09 04`.  
Payload CRC7 uses table `00 09 12 1B 24 2D 36 3F 48 41 5A 53 6C 65 7E 77`.

## Knob resolution

One turn is one full range. The 12-bit counter wraps one time for each turn: 4096 counts, or 1024
after you divide by four. The 1024 scale is the scale that a check screen shows the operator. The
project measured 65357 counts over five slow turns, within 0.3% of 65536.

The effect is more important than the number. A reader that samples slowly cannot tell a half turn
from a half turn backwards, because both put the counter in the same place. Past a quarter range for
each sample, the direction is a guess. So you must sample at the rate the board answers. If not, you
do not measure the rotation. You invent it.

## Knobs (hardware)

The knobs are Sensatec ELV-24 Y36A-K sensors. The ELV-24 Y36A-K is discontinued. Sensatec gives the
ELV-24 Y36G as the replacement. The two parts do not have the same output range, so do not assume one
range for both.

The sensor is a Hall-effect rotary sensor, not an incremental encoder. It turns 360 degrees with no
stop. It gives a linear analog voltage, from 0.07 V to 4.92 V, ±1% full scale. The output is
ratiometric: it follows the Vcc. The sensor has three pins: 1 = GND, 2 = OUT, 3 = Vcc. The connector
is a JST B3B-ZR (ZH series, 3 pins). It mates with a ZHR-3 header.

The board reads the OUT line with a 12-bit ADC, on the analog channels (CN13, ANALOG0 and ANALOG1).
So the value is an analog position, not a pulse count. A continuous turn makes a sawtooth: the value
rises to the maximum, then returns to zero. So the reader finds the movement by a wrapped difference,
not by a count of pulses. The ±16 jitter at rest is ADC noise.

## Reading rate and bad readings

The board makes about 469 records each second. Each answer carries the last seventeen records, so
each read has 36 ms of history. A faster poll gives nothing. At 624 requests each second, half of the
answers repeat the last cycle, and a block that the firmware writes during the read comes back torn.

About 0.5% of the records have a position some thousands of counts off the trend of their own window.
Sometimes two in a row are wrong. The median of the five newest records removes them. The records are
about 2 ms apart, so the median is the same position, without the bad values, 2 ms late. The cause of
the bad records is not known.

Note one trap. It cost one full turn out of five to find it. A reader that rejects a bad step also
removes the real movement in that step. The loss adds up without a sign: five slow turns measured
3.89. Hold the step back, and keep the reference in place. If the next reading comes back near the
reference, the step was noise, and you lose nothing. Two bad steps in a row mean the knob moved that
fast, so take the full movement.

A reader on Windows needs the same care for the timing. `Sleep(0)` gives the thread back to the
scheduler, which returns it one step later. A loop that asks for 300 Hz this way ran at 68 Hz, and
this added 15 ms to each input. `Sleep(1)` needs the system timer at 1 ms, and even then `Sleep`
cannot wake inside a millisecond. A spin loop works, but it uses a third of a core. A high-resolution
waitable timer (`CreateWaitableTimerExW` with `CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`, Windows 10
1803 and later) waits without a spin loop. The production reader uses it.

## Deobfuscation

The obfuscation is active when bit 4 of the flags byte is set. The keystream starts from the frame
tag, for each packet:

```python
x = tag ^ 0xAA
for b in payload:
    if (~b & 0xAA) & 0xFF == 0:      # every odd bit set
        yield b                       # unchanged, and the generator does NOT advance
        continue
    x = (x * 0x41C64E6D + 0x3039) & 0xFFFFFFFF
    yield b ^ (x & (0x55 if b & 0x80 else 0x7F))
```

Three details are important. Each one alone breaks the whole stream.

1. The mask applies to the keystream, not to the result.
2. The generator advances only on the bytes that it transforms. So no position-indexed keystream can
   match.
3. A byte with all odd bits set passes through without a change. So an idle payload, full of `0xFF`,
   looks like plaintext and hides the mechanism.

In substitution mode, the replacement byte also passes through the stream first, before the payload,
and this advances the stream. The compare then uses bytes that are already deobfuscated.

## Connectors

### CN13: analog inputs (7 pins)

| Pin | Signal | | Pin | Signal |
|---|---|---|---|---|
| 1 | ANALOG0 | | 5 | GND |
| 2 | n.c. | | 6 | n.c. |
| 3 | ANALOG1 | | 7 | 3.3 V |
| 4 | n.c. | | | |

Two analog inputs, no more. Channels 2 and 3 of the protocol reach no connector.

### CN11: first digital bank (24 pins)

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

### CN12: second digital bank (28 pins)

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

### CN18: LED data lines (16 pins)

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

Eight data lines for the LED strips.

**Pin 11 is an input, not an output.** The board does not make the 5 V for the strips. It expects
that rail here, and it distributes it. If you leave it unconnected, the data lines change, but every
strip stays dark.

### CN8: coin path (12 pins)

| Pin | Signal | | Pin | Signal |
|---|---|---|---|---|
| 1 | HOPPER_VCC (+5 V) | | 7 | GND |
| 2 | HOPPER_MOTOR_ON+ | | 8 | HOPPER_SENSOR |
| 3 | HOPPER_GND (+5 V) | | 9 | SELECT_ALARM |
| 4 | HOPPER_MOTOR_ON- | | 10 | SELECT_SEIKA |
| 5 | GND | | 11 | DIODE_P4_P6 |
| 6 | SELECT_BLOCKER | | 12 | +12 V |

### COM1: serial link (4 pins)

| Pin | Signal |
|---|---|
| 1 | TXD |
| 2 | RXD |
| 3 | GND |
| 4 | +12 V |

The board has 24 digital inputs and 24 outputs in total. A panel wires only a part of them. This is
why most of the 56-bit input field does not change.

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
| VOL-L / VOL-R | analog channels 0 / 1 | steady, ±16 of jitter | 0..65535, wraps one time for each turn |
| analog ch2, ch3 | analog channels 2, 3 | constant | never move, no connector |
| poll counter | block header byte 0 | goes up every cycle | flat means a dead link |
| bit 47 | record byte 5, bit 7 | always 0 | top bit of a 7-bit field, not an input |

## Outputs

The board has 24 outputs across three connectors: eight lamp drivers at 12 V, eight more outputs,
and eight data lines for the LED strips. Most strips are WS2812B at 5 V, from the board 5 V input
rail. A few are 12 V parts.

The outbound frame format is reversed and driven. `bi2x/encoder.py` builds the frames, and the panel
drives the LED strips and the button lamps. See [OUTPUTS.md](OUTPUTS.md) for the frame format, the
strip map and the lamp map.

## Known gaps

- The frames the panel builds are valid, but not byte-identical to the vendor's, so driving the
  hardware is not yet confirmed on the cabinet. See [OUTPUTS.md](OUTPUTS.md).
