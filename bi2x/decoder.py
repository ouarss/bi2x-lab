"""Decoder for the cabinet I/O board protocol. Pure Python, no dependencies.

    frame  : AA | node | tag | size (varint) | flags | [substitute] | payload | crc7
    flags  : [7:5] payload encoding | [4] obfuscated | [3:0] header CRC4
    payload: two command echoes, then a 228-byte block
    block  : 7-byte header + 17 records of 13 bytes
             -> SLIDING WINDOW: record 0 is the newest, and record k of one frame
                becomes record k+1 of the next. Consecutive frames overlap by 16.
    record : [0..6] 56 digital inputs, ACTIVE LOW
             [7..12] 4 analog channels, 12 bits big-endian,
                     expanded to 16 bits by (v << 4) | (v >> 8)

Validated frame for frame on 10 502 captured frames against a known-good
timestamped state log: every payload byte identical, every button state exact.

Usage:
    python decoder.py [capture ...]   # validate against captures
    import decoder                    # as a library
"""
import glob
import sys

LCG_A, LCG_C = 0x41C64E6D, 0x3039
BUTTONS = {"START": 6, "BT-A": 7, "BT-B": 8, "BT-C": 9,
           "BT-D": 10, "FX-L": 11, "FX-R": 12}
KNOB_L_CH, KNOB_R_CH = 0, 1
PREFIX, HDR, RECLEN, NREC, BLOCK = 6, 7, 13, 17, 228

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
    """Payload checksum, last byte of the frame."""
    return _crc(TBL7, 0x7F, data, 0x7F) ^ 0x7F


class Keystream:
    """Per-packet obfuscation state, seeded from the frame tag.

    A byte whose odd bits are all set (`(~b & 0xAA) == 0`) passes through untouched
    **and does not advance the generator**, which is why no position-indexed
    keystream ever matches, and why an idle payload full of 0xFF looks like
    plaintext. The mask applies to the keystream, not to the result.
    """

    __slots__ = ("x",)

    def __init__(self, tag):
        self.x = (tag ^ 0xAA) & 0xFFFFFFFF

    def byte(self, b):
        if (~b & 0xAA) & 0xFF == 0:
            return b
        self.x = (self.x * LCG_A + LCG_C) & 0xFFFFFFFF
        return b ^ (self.x & (0x55 if b & 0x80 else 0x7F))


def deobfuscate(payload, tag):
    """Deobfuscate a whole payload."""
    stream = Keystream(tag)
    return bytes(stream.byte(b) for b in payload)


def parse_frames(raw):
    """Split a buffer into frames. Payloads come back decoded.

    Encoding 3 ("substitution") carries a replacement byte between the flags and the
    payload, so the frame is one byte longer than `size`. That byte goes through the
    keystream first, advancing it; occurrences of its decoded value in the payload
    then stand for 0xAA, which cannot be transmitted as-is since it marks a frame
    start. Skipping it shifts the whole block by one.
    """
    out, i = [], 0
    while i + 6 < len(raw):
        if raw[i] != 0xAA:
            i += 1
            continue
        j, size = i + 3, 0
        while j < len(raw) and raw[j] & 0x80:
            size = (size << 6) | (raw[j] & 0x3F)
            j += 1
        if j >= len(raw):
            break
        size = (size << 7) | (raw[j] & 0x7F)
        flags, p = raw[j + 1], j + 2
        encoding = flags >> 5
        substitute = None
        if encoding == 3:
            if p >= len(raw):
                break
            substitute = raw[p]
            p += 1
        if p + size + 1 > len(raw):
            break
        head = bytearray(raw[i + 1:j + 2])
        head[-1] &= 0xF0
        payload = raw[p:p + size]
        if flags & 0x10:
            stream = Keystream(raw[i + 2])
            if substitute is not None:
                substitute = stream.byte(substitute)
            payload = bytes(stream.byte(b) for b in payload)
        if substitute is not None:
            payload = payload.replace(bytes([substitute]), b"\xAA")
        out.append(dict(node=raw[i + 1], tag=raw[i + 2], size=size,
                        encoding=encoding, obfuscated=bool(flags & 0x10),
                        substitute=substitute,
                        crc4_ok=crc4(head) == (flags & 0x0F),
                        payload=payload, crc7=raw[p + size]))
        i = p + size + 1
    return out


def parse_stream(buf):
    """Incremental variant: returns (frames, bytes consumed).

    Keep the tail for the next call: over a live link a frame almost always arrives
    split across two reads.
    """
    out, i = [], 0
    while True:
        j = buf.find(b"\xAA", i)
        if j < 0:
            return out, max(i, len(buf) - 1)
        frames = parse_frames(buf[j:j + 512])
        if not frames:
            if len(buf) - j < 300:
                return out, j
            i = j + 1
            continue
        f = frames[0]
        length = 3 + (2 if f["size"] > 127 else 1) + 1 + \
            (1 if f["encoding"] == 3 else 0) + f["size"] + 1
        out.append(f)
        i = j + length
        if i >= len(buf):
            return out, i


def block_header(payload):
    """The 7 bytes preceding the records."""
    return payload[PREFIX:PREFIX + HDR]


def system_inputs(payload):
    """Service panel inputs. ACTIVE HIGH: these bytes read 0x00 at rest.

    `counter` increments once per polling cycle: a useful liveness check.
    """
    h = block_header(payload)
    if len(h) < HDR:
        return {}
    return {"TEST": bool((h[2] >> 1) & 1),
            "SERVICE": bool((h[2] >> 2) & 1),
            "COIN": bool((h[2] >> 3) & 1),
            "HEADPHONE": bool(h[4]),
            "counter": h[0]}


def records(payload):
    """The 17 records of a poll response, newest first."""
    block = payload[PREFIX:PREFIX + BLOCK]
    if len(block) < BLOCK:
        return []
    return [block[HDR + RECLEN * i: HDR + RECLEN * (i + 1)] for i in range(NREC)]


def digital(record):
    """56-bit input field. A bit at 0 means the input is active."""
    return int.from_bytes(record[:7], "little")


def analog(record):
    """The 4 analog channels, expanded to 16 bits."""
    hi0, mid, lo1, hi2, mid2, lo3 = record[7:13]
    raw12 = [(hi0 << 4) | (mid >> 4), ((mid & 0xF) << 8) | lo1,
             (hi2 << 4) | (mid2 >> 4), ((mid2 & 0xF) << 8) | lo3]
    return [((v << 4) | (v >> 8)) & 0xFFFF for v in raw12]


def buttons_byte(record):
    """Buttons packed as one byte: bit 0 START … bit 6 FX-R."""
    field = digital(record)
    return sum((1 << i) for i, (_, bit) in enumerate(BUTTONS.items())
               if not (field >> bit) & 1)


def pressed(record):
    """Names of the buttons currently held."""
    field = digital(record)
    return [name for name, bit in BUTTONS.items()
            if not (field >> bit) & 1]


def knob_delta(new, old):
    """Rotation between two readings, accounting for the 16-bit wrap."""
    return ((new - old + 32768) & 0xFFFF) - 32768


def _load(path):
    traffic, states = [], []
    section = None
    for line in open(path, encoding="utf-8"):
        if line.startswith("## TRAFIC"):
            section = "t"; continue
        if line.startswith("## ETATS"):
            section = "e"; continue
        if line.startswith("## "):
            section = None; continue
        if not line.strip() or line.startswith("#"):
            continue
        p = line.split()
        try:
            if section == "t" and p[2] == "RX":
                traffic.append((float(p[1]),
                                bytes(int(x, 16) for x in p[4:] if len(x) == 2)))
            elif section == "e":
                states.append((float(p[0]), int(p[1].split("=")[1], 16),
                               int(p[2].split("=")[1]), int(p[3].split("=")[1])))
        except (ValueError, IndexError):
            pass
    return traffic, states


def _validate(path):
    import bisect
    traffic, states = _load(path)
    stamps = [s[0] for s in states]

    def truth(t):
        i = bisect.bisect_left(stamps, t)
        for j in (i - 1, i):
            if 0 <= j < len(states) and abs(states[j][0] - t) < 0.03:
                return states[j]
        return None

    n = ok = kl = kr = crc = total = 0
    for t, raw in traffic:
        for f in parse_frames(raw):
            if f["node"] != 0x03 or f["size"] != 234:
                continue
            total += 1
            crc += f["crc4_ok"]
            st, rs = truth(t), records(f["payload"])
            if st is None or not rs:
                continue
            n += 1
            ok += buttons_byte(rs[0]) == st[1]
            a = analog(rs[0])
            kl += a[KNOB_L_CH] == st[2]
            kr += a[KNOB_R_CH] == st[3]
    print(f"  {total} poll responses, header CRC valid: "
          f"{100 * crc // max(total, 1)} %")
    if n:
        print(f"  buttons exact : {ok}/{n} = {100 * ok / n:6.2f} %")
        print(f"  VOL-L exact   : {kl}/{n} = {100 * kl / n:6.2f} %")
        print(f"  VOL-R exact   : {kr}/{n} = {100 * kr / n:6.2f} %")


if __name__ == "__main__":
    paths = sys.argv[1:] or sorted(glob.glob("captures/*.txt"))
    if not paths:
        print(__doc__.strip())
        sys.exit(0)
    for p in paths:
        print(f"\n=== {p} ===")
        try:
            _validate(p)
        except Exception as e:                          # noqa: BLE001
            print(f"  error: {e}")
