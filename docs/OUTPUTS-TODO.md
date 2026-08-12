← [README](../README.md)

# Roadmap: drive the outputs

Status: the inputs are done. The outputs (the button lamps and the LED strips) are documented, but
the tools do not drive them. Nothing is written to the board. This page is the plan for later.

## Goal

Drive the outputs: the button lamps (12 V, CN11 OUTPUT_A) and the LED strips (data on CN18
OUTPUT_F). Make the panel toggles light the hardware.

## Why it is not done

To write to the board, the tools must build outbound frames. The outbound command format is not
known. The read direction (inbound) is solved. The write direction (outbound) is not.

## Plan

1. Capture the outbound frames. Run the vendor software. Use the operator TEST menu (`LAMP CHECK`,
   `AUTO` mode) and the attract mode. These drive every output, one at a time, in a named order.
   Record the host-to-board serial direction (see [CAPTURE.md](CAPTURE.md) for the method, and
   [TEST-MENU.md](TEST-MENU.md) for the screen, the output map and the AUTO sequence).
2. Match each frame to its output. The test menu changes one output at a time. So you can map one
   frame to one lamp or one LED.
3. Reverse the outbound frame format. Find the node, the command and the payload layout. Find how a
   frame carries a lamp bit and an LED colour. Check if the outbound payload uses the same
   obfuscation and CRC as the inbound frames.
4. Write an encoder in `bi2x/`. It builds an outbound frame from a wanted output state. It is the
   mirror of `decoder.py`.
5. Wire it into `panel.py`. The `POST /output` route stores the wanted state now, and answers
   `supported: false`. Make it build the frame and send it.
6. Test on hardware.

## Open points

- The outbound payload may be compressed, like the request payload. Check this first.
- The board may send an answer or an ACK to a write. Check this.
- Power: CN18 pin 11 (+5V_IN) must supply the 5 V rail for the strips. If it is not connected, the
  data lines change, but every strip stays dark.
- Keep all captures private, like `replay.json`.
