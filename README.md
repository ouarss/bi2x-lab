# bi2x-lab

This project reads the inputs of an arcade rhythm-game control panel. The inputs are the buttons,
the service switches and the two 360-degree volume knobs. The project reads them from the cabinet
I/O board, over the USB serial link. No vendor library is necessary.

The board uses a framed protocol with a per-packet obfuscation layer. This repository documents the
protocol. It also has a small decoder with few dependencies.

Status:

- The inputs are fully decoded and tested on hardware.
- The outputs (the button lamps and the LED strips) are documented. The project does not drive them
  yet.

The project validated the decoder on 10502 captured frames against a timestamped state log. 100% of
the payloads are the same byte for byte. 100% of the button states are correct. The header CRC is
correct on every frame of four separate captures.

For the protocol, the connectors and the signal map, see [docs/TECHNICAL.md](docs/TECHNICAL.md).

## What it decodes

| Signal | Notes |
|---|---|
| START, BT-A to BT-D, FX-L, FX-R | 7 buttons, active low |
| VOL-L / VOL-R | 12-bit absolute encoders, they wrap, expanded to 16 bits |
| TEST / SERVICE / COIN MECH | service panel |
| HEADPHONE | jack detection |
| 56-bit input field, 4 analog channels | this cabinet wires only 7 bits and 2 channels |

## Run the tools

The decoder has no dependencies. It works offline on a captured stream. The live tools need
`pyserial`.

```
pip install pyserial
python bi2x/panel.py            # then open http://127.0.0.1:8740
python bi2x/client.py           # console output
```

The serial port is exclusive. Close all other programs that use the board first.

The image below shows the web panel.

![The bi2x-lab web panel, live view](media/web-interface-preview.png)

The live tools send request frames to poll the board. The board request payload is compressed, and
this project does not implement that compression. So the tools replay recorded requests from
`bi2x/replay.json`. This project does not distribute that file. Make your own from a capture of your
cabinet. The decoder does not need this file. It decodes any stream that you already have.

## Layout

```
bi2x/decoder.py   framing, CRCs, deobfuscation, block and record access
bi2x/client.py    console client, shows the inputs live
bi2x/panel.py     local web server for the panel view
bi2x/web/         the panel page (HTML, CSS, JS, no build step, no CDN)
```

## License

MIT. See [LICENSE](LICENSE). The license is permissive on purpose. The point of this work is that
other people can reuse it.
