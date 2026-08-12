"""Encoder for the cabinet I/O board protocol (host->board, node 0x02). Pure Python.

The mirror of `decoder.py`: it builds an outbound frame from a wanted output state.
The read direction is solved by `decoder.py`; this closes the write direction, so the
tools can drive the button lamps and the LED strips.

    frame  : AA | node | tag | size(varint) | flags | payload_on_wire | crc7
    flags  : [7:5] encoding mode | [4] obfuscated | [3:0] header CRC4
    size   : varint, length of the DECOMPRESSED payload
    crc7   : computed on the plaintext, then carried THROUGH the obfuscation keystream
             (it is the last byte of the stream, deobfuscated with the payload)

Obfuscation is the same LCG keystream as the inbound frames, but seeded on `tag ^ 0x55`
(the inbound direction uses `tag ^ 0xAA`). The keystream covers the payload and the crc7.

Encoding mode 4 is a sliding-window LZ (85-byte window, write pointer starts at 81).
The board accepts any valid mode-4 stream, so this greedy compressor drives the hardware
even though it does not reproduce the vendor compressor byte for byte. Mode 2 sends the
payload raw and is valid only when the payload holds no 0xAA (the frame start marker).

The LED command is SetTapeLedData:

    03 21 | strip | 00 | offset(2 LE) | count | count*2 bytes of pixels

Each pixel is a 15-bit 5-5-5 RGB colour, little-endian 16-bit, bit 15 = 0.
"""
LCG_A, LCG_C = 0x41C64E6D, 0x3039
TX_SEED_XOR = 0x55
WINDOW, WP_INIT = 85, 81

TBL4 = [0x00, 0x0D, 0x03, 0x0E, 0x06, 0x0B, 0x05, 0x08,
        0x0C, 0x01, 0x0F, 0x02, 0x0A, 0x07, 0x09, 0x04]
TBL7 = [0x00, 0x09, 0x12, 0x1B, 0x24, 0x2D, 0x36, 0x3F,
        0x48, 0x41, 0x5A, 0x53, 0x6C, 0x65, 0x7E, 0x77]


def _crc(table, crc, data, mask):
    crc &= mask
    for b in data:
        t = table[(b ^ crc) & 0x0F]
        crc = (((crc >> 4) ^ t) >> 4) ^ table[(((crc >> 4) ^ t) ^ (b >> 4)) & 0x0F]
    return crc & mask


def crc4(data):
    """Header checksum, carried in the low nibble of the flags byte."""
    return _crc(TBL4, 0x0F, data, 0x0F) ^ 0x0F


def crc7(data):
    """Payload checksum, computed on the plaintext."""
    return _crc(TBL7, 0x7F, data, 0x7F) ^ 0x7F


def obfuscate(tag, data):
    """Symmetric LCG keystream, seeded on tag ^ 0x55. A byte whose odd bits are all
    set passes through and leaves the generator idle (same rule as the decoder)."""
    x = (tag ^ TX_SEED_XOR) & 0xFFFFFFFF
    out = bytearray()
    for b in data:
        if (~b & 0xAA) & 0xFF == 0:
            out.append(b)
            continue
        x = (LCG_A * x + LCG_C) & 0xFFFFFFFF
        out.append(b ^ (x & (0x55 if b & 0x80 else 0x7F)))
    return bytes(out)


def decompress(src):
    """Mode-4 inflate. Kept so the encoder can self-check what it produced."""
    out = bytearray()
    win = bytearray(WINDOW)
    wp = WP_INIT

    def emit(b):
        nonlocal wp
        out.append(b)
        win[wp] = b
        wp = (wp + 1) % WINDOW

    i = 0
    while i < len(src):
        flag = src[i]
        i += 1
        pos = 0
        while pos <= 6:
            if (flag >> pos) & 1 == 0:
                pos += 1
                if i >= len(src):
                    return bytes(out)
                emit(src[i])
                i += 1
            else:
                second = (flag >> (pos + 1)) & 1
                pos += 2
                if second:
                    emit(0xAA)
                else:
                    if i >= len(src):
                        return bytes(out)
                    o = src[i]
                    i += 1
                    if o >= 0xAB:
                        length, off = 4, o - 0xAB
                    elif o >= 0x55:
                        length, off = 3, o - 0x55
                    else:
                        length, off = 2, o
                    for k in range(length):
                        emit(win[(off + k) % WINDOW])
    return bytes(out)


def compress(data):
    """Greedy mode-4 deflate that always inflates back to `data`. Falls back to a
    literal-only stream if the greedy pass ever fails its own check."""
    win = bytearray(WINDOW)
    wp = WP_INIT
    tokens = []
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        best = None
        if b != 0xAA:
            for off in range(WINDOW):
                length = 0
                while length < 4 and i + length < n and \
                        win[(off + length) % WINDOW] == data[i + length]:
                    length += 1
                if length >= 2 and (best is None or length > best[0]):
                    best = (length, off)
        if best:
            length, off = best
            tokens.append(("m", off, length))
            for k in range(length):
                win[wp] = data[i + k]
                wp = (wp + 1) % WINDOW
            i += length
        else:
            tokens.append(("a",) if b == 0xAA else ("l", b))
            win[wp] = b
            wp = (wp + 1) % WINDOW
            i += 1

    out = bytearray()
    j = 0
    while j < len(tokens):
        flag = 0
        body = bytearray()
        pos = 0
        while pos <= 6 and j < len(tokens):
            tk = tokens[j]
            if tk[0] == "l":
                body.append(tk[1])
                pos += 1
            elif tk[0] == "a":
                flag |= (3 << pos) & 0xFF
                pos += 2
            else:
                flag |= (1 << pos) & 0xFF
                pos += 2
                _, off, length = tk
                tokv = 0x55 * (length - 2) + off
                if tokv >= 0xAA:
                    tokv += 1
                body.append(tokv & 0xFF)
            j += 1
        out.append(flag)
        out += body

    result = bytes(out)
    if decompress(result) != data:
        return _compress_literal(data)
    return result


def _compress_literal(data):
    """Trivial always-valid mode-4 stream: literals and reserved-0xAA only."""
    out = bytearray()
    i = 0
    while i < len(data):
        flag = 0
        body = bytearray()
        pos = 0
        while pos <= 6 and i < len(data):
            b = data[i]
            if b == 0xAA:
                flag |= (3 << pos) & 0xFF
                pos += 2
            else:
                body.append(b)
                pos += 1
            i += 1
        out.append(flag)
        out += body
    return bytes(out)


def _write_varint(value):
    low = value & 0x7F
    v = value >> 7
    groups = []
    while v > 0:
        groups.append(0xC0 | (v & 0x3F))
        v >>= 6
    return bytes(reversed(groups)) + bytes([low])


def encode_frame(tag, payload, node=0x02, mode=4):
    """Build a complete, board-acceptable frame from a plaintext payload."""
    if mode == 4:
        wire = compress(payload)
    elif mode == 2:
        if 0xAA in payload:
            raise ValueError("mode 2 cannot carry 0xAA; use mode 4")
        wire = bytes(payload)
    else:
        raise ValueError("encode_frame supports modes 2 and 4")
    size = _write_varint(len(payload))
    flags = (mode << 5) | 0x10
    head = bytearray([node, tag]) + size + bytes([flags & 0xF0])
    flags |= crc4(head)
    stream = bytes(wire) + bytes([crc7(payload)])
    return bytes([0xAA, node, tag]) + size + bytes([flags]) + obfuscate(tag, stream)


# ---------------------------------------------------------------- LED strips
def pack_pixel(r5, g5, b5):
    """One 15-bit 5-5-5 RGB pixel as two little-endian bytes."""
    word = ((r5 & 0x1F) << 10) | ((g5 & 0x1F) << 5) | (b5 & 0x1F)
    return bytes([word & 0xFF, (word >> 8) & 0xFF])


def rgb_to_555(r8, g8, b8, brightness=1.0):
    """An 8-bit RGB colour, scaled by brightness (0..1), quantised to 5 bits each."""
    scale = max(0.0, min(1.0, brightness)) * 31 / 255
    return (round(r8 * scale), round(g8 * scale), round(b8 * scale))


def tape_led_payload(strip, pixels, offset=0):
    """SetTapeLedData plaintext for one strip. `pixels` is a list of (r5, g5, b5)."""
    body = bytearray([0x03, 0x21, strip & 0xFF, 0x00,
                      offset & 0xFF, (offset >> 8) & 0xFF, len(pixels) & 0xFF])
    for r5, g5, b5 in pixels:
        body += pack_pixel(r5, g5, b5)
    return bytes(body)


def tape_led_frame(tag, strip, pixels, offset=0):
    """A ready-to-send frame that sets `pixels` on `strip` from `offset`."""
    return encode_frame(tag, tape_led_payload(strip, pixels, offset))
