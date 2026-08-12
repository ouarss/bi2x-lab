← [README](../README.md)

# The operator test menu, and what to capture from it

This page records the cabinet operator test menu, screen by screen. The menu is a directed signal
generator: each screen drives a known set of inputs or outputs, one at a time, with the state shown
on screen. That makes it the reference source for a capture.

The main use is the outputs. The board inputs are already solved (see
[TECHNICAL.md](TECHNICAL.md)). The outputs (the button lamps and the LED strips) are not. The
`LAMP CHECK` screen drives every output in a known order, so it is the way to reverse the outbound
frame format. See the plan in [OUTPUTS-TODO.md](OUTPUTS-TODO.md), and the capture methods in
[CAPTURE.md](CAPTURE.md).

The screens below are from this cabinet. Names and values are transcribed as shown, so you do not
have to open the menu again.

## How to reach the menu

Enter the operator test mode (the `TEST` service switch). The root test menu lists:

```
I/O CHECK          SCREEN CHECK       COLOR CHECK
ROM CHECK          IC CARD CHECK      SOUND OPTIONS
GAME OPTIONS       COIN OPTIONS       NETWORK OPTIONS
BOOKKEEPING        CLOCK              ALL FACTORY SETTINGS
SYSTEM INFORMATION SOFTWARE LICENSE INFORMATION
GAME MODE
```

`I/O CHECK` is the one that matters for this project. Its submenu:

```
INPUT CHECK        TOUCH PANEL CHECK  CAMERA CHECK
LAMP CHECK         MECHANISM CHECK    SWITCH COUNTER CHECK
CALIBRATION SETTINGS
EXIT
```

## INPUT CHECK

Shows every board input live. This is the read direction, which the decoder already covers. Use it
to confirm the wiring and the value scale, not to reverse anything new.

| Line | Values | Note |
|---|---|---|
| TEST | OFF / ON | service switch |
| SERVICE | OFF / ON | service switch |
| START BUTTON | OFF / ON | |
| A / B / C / D BUTTON | OFF / ON | BT-A to BT-D |
| FX L / FX R BUTTON | OFF / ON | |
| COIN MECH | OFF / ON | |
| ANALOG VOLUME L / R | 0 to 1023, with a bar | the two knobs |
| HEADPHONE | OFF / ON | jack detection |

Two facts confirmed here:

- The knob value on screen tops out at 1023. This is the 0..1023 graduation scale, not the raw
  16-bit value. It matches `GRADUATIONS_PER_REVOLUTION = 1024` in the decoder.
- The knob bar is red, and fills green as the value rises. Scrolling the knob through its range
  fills and empties the bar. A jump in the bar shows a bad reading (a skip), which is the same ADC
  noise that the decoder removes with a median.

## LAMP CHECK

This is the important screen. It drives the outputs. Two modes at the top: `ALL` (selected by
default) and `AUTO`.

The outputs are two groups. The RGB LED zones (a colour each), then the button lamps (on or off):

| RGB LED zone | Button lamp |
|---|---|
| TITLE | START BUTTON |
| UPPER LEFT SPEAKER | BT A BUTTON |
| UPPER RIGHT SPEAKER | BT B BUTTON |
| LEFT WING | BT C BUTTON |
| RIGHT WING | BT D BUTTON |
| LOWER LEFT SPEAKER | FX L BUTTON |
| LOWER RIGHT SPEAKER | FX R BUTTON |
| CONTROL PANEL | |
| WOOFER | |
| V UNIT | |
| CARD READER | |

That is 11 RGB zones and 7 button lamps, 18 outputs in total. This list is the output map of the
panel. Cross it with the connector pinout (CN11 lamps, CN18 LED data lines) in
[TECHNICAL.md](TECHNICAL.md).

### ALL mode

Sends the same state to every output at once: `WHITE` to all 11 RGB zones, `ON` to all 7 button
lamps. Good to prove the wiring and the 5 V rail (CN18 pin 11). It is one static state, so it is a
weak signal for reversing the frame format.

### AUTO mode (use this one to reverse the outputs)

Walks the list from top to bottom. For each RGB zone, one zone is lit at a time, and it cycles
through the colours in this order:

```
WHITE  ->  RED  ->  GREEN  ->  BLUE
```

The button lamps only turn `ON`. At the end it sets every output to `WHITE`, then it starts again,
in a loop.

This sequence is why AUTO is the key. Only one output changes at a time, and the screen names it and
gives its colour. So each outbound frame in the capture maps to one (zone, colour) with no guessing.
Let it loop several times: the repeats confirm the mapping and average out any dropped frame. This is
step 1 and 2 of the outputs plan in [OUTPUTS-TODO.md](OUTPUTS-TODO.md).

## MECHANISM CHECK

One line only: `COIN BLOCKER`, shown `OPEN` or `CLOSED`. It drives the coin blocker solenoid. It is
a second, isolated output. Capturing it gives one more known outbound frame, separate from the LED
path.

## CALIBRATION SETTINGS

Sets the range of the two knobs. You do this one time. The screen shows, per knob, the live value, a
bar, and `COUNT`, `MIN`, `MAX`.

The procedure, in order, one press of `START` between each step:

1. Turn `VOL-L` slowly counterclockwise more than 3 times. When `COUNT = OK` appears, press START.
2. Turn `VOL-L` slowly clockwise more than 3 times. Press START.
3. Turn `VOL-R` slowly counterclockwise more than 3 times. Press START.
4. Turn `VOL-R` slowly clockwise more than 3 times. Press START.

`MIN` and `MAX` record the extremes the ADC saw during the turns. On this cabinet the calibration
settled near:

| Knob | MIN | MAX |
|---|---|---|
| VOL-L | 7 | 1012 |
| VOL-R | 9 | 1009 |

So the usable range is not the full 0..1023. This confirms the sawtooth model: a continuous turn
sweeps the value from `MIN` to `MAX`, then wraps. The calibration finds the two ends of that ramp.
It is a good value to record for a cabinet, because a replacement knob (the ELV-24 Y36G) does not
have the same range.

## What to capture, in short

| Screen | Direction | Why |
|---|---|---|
| LAMP CHECK, AUTO | host to board (TX) | reverse the outbound LED frame, one zone and colour at a time |
| LAMP CHECK, ALL | host to board (TX) | the all-on state, a simple cross-check |
| MECHANISM CHECK | host to board (TX) | the coin blocker output, isolated |
| the boot handshake | both | see if enabling outputs changes the init sequence |
| INPUT CHECK | board to host (RX) | already solved, use only to confirm |

For the capture methods (a serial proxy, or a hook on the vendor driver), and for how to keep the
raw files private, see [CAPTURE.md](CAPTURE.md).
