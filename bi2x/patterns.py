"""The lighting patterns the cabinet plays, rebuilt so the panel can play them.

The game addresses the strips as one 428-LED span cut into 10 named zones, and
runs one pattern at a time. A pattern keeps no state but the seconds elapsed
since it started, so it is a pure function of time and replays exactly here.
See docs/OUTPUTS.md for the zone map and the pattern list.

Colours are RGBA floats while a frame is being drawn, because the blend modes
use the alpha; `to_strips` quantises the result to the 5 bits the wire carries.

No dependency.
"""
import math

# ------------------------------------------------------------------ the map

# (name, offset in the 428-LED span, LED count)
ZONES = [
    ("TITLE", 0, 74),
    ("SPEAKER_L1", 74, 12),
    ("SPEAKER_R1", 86, 12),
    ("WING_L", 98, 56),
    ("WING_R", 154, 56),
    ("CONPANE", 210, 94),
    ("SPEAKER_L2", 304, 12),
    ("SPEAKER_R2", 316, 12),
    ("WOOFER", 328, 14),
    ("V_UNIT", 342, 86),
]
LED_COUNT = 428
STRIP_LEDS = [74, 12, 12, 56, 56, 94, 38, 86]

FRAME_STEP = 1.0 / 60.0          # the rate the patterns are written for

# The five colours every pattern is built from.
BLACK = (0.0, 0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0, 1.0)
CYAN = (0.0, 1.0, 1.0, 1.0)
PINK = (1.0, 0.0, 0.7, 1.0)
PURPLE = (0.7, 0.0, 1.0, 1.0)

# The rainbow primitive walks this wheel, one colour per LED, white first.
WHEEL = [WHITE, (1.0, 0.0, 0.0, 1.0), (1.0, 1.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0),
         CYAN, (0.0, 0.0, 1.0, 1.0), (1.0, 0.0, 1.0, 1.0)]


def clamp(x, low=0.0, high=1.0):
    return max(low, min(high, x))


def lerp(c0, c1, w):
    return tuple(c0[k] + (c1[k] - c0[k]) * w for k in range(4))


def hsv_to_rgb(h, s, v):
    """Six-sector HSV, h in 0..1."""
    i = int(math.floor(h * 6.0)) % 6
    f = h * 6.0 - math.floor(h * 6.0)
    p, q, t = v * (1.0 - s), v * (1.0 - f * s), v * (1.0 - (1.0 - f) * s)
    return [(v, t, p), (q, v, p), (p, v, t),
            (p, q, v), (t, p, v), (v, p, q)][i]


# ---------------------------------------------------------------- the frame

class Frame:
    """The 428 LEDs as RGBA floats, addressed by zone."""

    def __init__(self):
        self.leds = [BLACK] * LED_COUNT

    def zone(self, index):
        _, offset, count = ZONES[index]
        return offset, count

    def fill(self, index, colour):
        offset, count = self.zone(index)
        for i in range(offset, offset + count):
            self.leds[i] = colour

    def fill_range(self, index, start, end, colour):
        offset, count = self.zone(index)
        start, end = max(0, min(count, start)), max(0, min(count, end))
        for i in range(offset + start, offset + end):
            self.leds[i] = colour

    def clear(self):
        self.leds = [BLACK] * LED_COUNT


def blend(frame, index, mode, source, start, end):
    """Compose `source` onto a zone. Six modes, as the cabinet has them."""
    offset, count = frame.zone(index)
    start, end = max(0, min(count, start)), max(0, min(count, end))
    for i in range(start, end):
        d, s = frame.leds[offset + i], source[i]
        if mode == 0:
            r = s
        elif mode == 1:
            r = tuple(d[k] + s[k] for k in range(4))
        elif mode == 2:
            r = tuple(d[k] - s[k] for k in range(4))
        elif mode == 3:
            r = tuple(d[k] * s[k] for k in range(4))
        elif mode == 4:
            r = tuple(d[k] + s[k] * s[3] for k in range(4))
        elif mode == 5:
            r = tuple(d[k] + (s[k] - d[k]) * s[3] for k in range(4))
        else:
            raise ValueError(f"unknown blend mode: {mode}")
        frame.leds[offset + i] = r


def _bounds(count, f1, f2):
    """Segment bounds in LED indices, rounded the way the cabinet rounds them."""
    low, high = (f1, f2) if f1 <= f2 else (f2, f1)
    x0, x1 = count * low, count * high
    return x0, x1, max(0, min(count, int(x0 + 0.01))), max(0, min(count, int(x1 + 0.01)))


# ----------------------------------------------------------- the primitives

class Rainbow:
    """State of the per-LED rainbow: a wheel index, a level, a saturation."""

    def __init__(self, level, saturation, index=0):
        self.index = index
        self.level = level
        self.saturation = saturation


def rainbow(frame, index, mode, state):
    """One wheel colour per LED, desaturated towards white and scaled."""
    _, count = frame.zone(index)
    a, b = state.level, state.saturation
    source = []
    for _ in range(count):
        c = lerp(WHITE, WHEEL[state.index % 7], b)
        state.index += 1
        source.append((c[0] * a, c[1] * a, c[2] * a, c[3]))
    blend(frame, index, mode, source, 0, count)


def segment_sin(frame, index, f1, f2, mode, colours, phase1, phase2):
    """Paint f1..f2 of a zone, weighted by abs(sin(pi/2 * phase)).

    f1 and f2 are fractions of the zone; phase1 goes with f1, phase2 with f2.
    """
    _, count = frame.zone(index)
    x0, x1, i0, i1 = _bounds(count, f1, f2)
    p0, p1 = (phase1, phase2) if f1 <= f2 else (phase2, phase1)
    if i0 >= i1:
        return
    step = 1.0 / (x1 - x0)
    source = [None] * count
    for i in range(i0, i1):
        u = (i - x0) * step
        w = abs(math.sin((p0 + u * (p1 - p0)) * (math.pi / 2.0)))
        source[i] = lerp(colours[0], colours[1], w)
    blend(frame, index, mode, source, i0, i1)


def segment_lin(frame, index, f1, f2, mode, colours, weight1, weight2):
    """The same, weighted linearly and clamped."""
    _, count = frame.zone(index)
    x0, x1, i0, i1 = _bounds(count, f1, f2)
    p0, p1 = (weight1, weight2) if f1 <= f2 else (weight2, weight1)
    if i0 >= i1:
        return
    step = 1.0 / (x1 - x0)
    source = [None] * count
    for i in range(i0, i1):
        source[i] = lerp(colours[0], colours[1], clamp(p0 + (i - x0) * step * (p1 - p0)))
    blend(frame, index, mode, source, i0, i1)


def segment_ramp(frame, index, f1, f2, mode, colours, weight):
    """Linear segment whose weight starts at 0 on the f1 side."""
    segment_lin(frame, index, f1, f2, mode, colours, 0.0, weight)


# -------------------------------------------------------------- the painters
#
# Three of them, each taking the 10 zones and one intensity. A and B share the
# span without overlapping, except on the control panel where B adds over A.
# C replaces B in the fade patterns.

def painter_a(frame, a):
    """TITLE, CONPANE, WOOFER and V-UNIT, in white and cyan."""
    white_a, cyan_a = (a, a, a, a), (0.0, a, a, a)
    white_1, cyan_1 = (a, a, a, 1.0), (0.0, a, a, 1.0)

    rainbow(frame, 0, 1, Rainbow(0.4, 0.6))
    for f1, f2, p1, p2 in ((0.608108, 0.297297, 0.0, 1.0),
                           (0.297297, 0.0, 1.0, 0.0),
                           (0.608108, 0.810811, 0.0, 1.0),
                           (0.810811, 1.0, 1.0, 0.0)):
        segment_sin(frame, 0, f1, f2, 5, (cyan_a, white_a), p1, p2)

    rainbow(frame, 5, 0, Rainbow(0.4, 0.4))

    segment_ramp(frame, 8, 0.0, 0.5, 5, (white_1, cyan_1), 1.0)
    segment_ramp(frame, 8, 0.5, 1.0, 5, (cyan_1, white_1), 1.2)

    rainbow(frame, 9, 0, Rainbow(0.3, 0.5))
    segment_sin(frame, 9, 0.55814, 1.0, 5, (white_1, cyan_1), 0.0, 1.0)
    segment_sin(frame, 9, 0.465116, 0.0, 5, (white_1, cyan_1), 0.0, 1.0)
    segment_sin(frame, 9, 0.465116, 0.55814, 5, (white_1, cyan_1), 0.5, 0.5)


def painter_b(frame, a):
    """The wings, the control panel and the four speakers. Intensity tints."""
    b, c = a * 0.8 + 0.2, a * 0.7
    white_b, cyan_b = (b, b, b, 1.0), (0.0, b, b, 1.0)
    purple_b = (0.7 * b, 0.0, b, 1.0)
    cyan_c = (0.0, c, c, 1.0)
    white_a, cyan_a = (a, a, a, 1.0), (0.0, a, a, 1.0)

    for z in (3, 4):
        segment_lin(frame, z, 0.0, 0.5, 1, (cyan_b, white_b), 0.0, 1.0)
    for z in (3, 4):
        segment_lin(frame, z, 1.0, 0.5, 1, (purple_b, white_b), 0.0, 0.8)

    segment_lin(frame, 5, 0.0, 0.273684, 4, (BLACK, cyan_c), 0.5, 1.0)
    segment_sin(frame, 5, 0.273684, 0.589474, 4, (cyan_c, cyan_c), 1.0, 0.0)
    segment_lin(frame, 5, 0.589474, 0.852632, 4, (BLACK, cyan_c), 1.0, 0.5)
    segment_sin(frame, 5, 0.852632, 1.0, 5, (cyan_c, cyan_c), 0.0, 1.0)

    segment_lin(frame, 1, 1.0, 0.5, 0, (white_a, cyan_a), 0.0, 1.0)
    segment_lin(frame, 1, 0.0, 0.5, 0, (white_a, cyan_a), 0.0, 1.0)
    segment_lin(frame, 2, 1.0, 0.5, 0, (white_a, cyan_a), -0.5, 1.0)
    segment_lin(frame, 2, 0.0, 0.5, 0, (white_a, cyan_a), 0.0, 1.0)

    half = lerp(cyan_a, white_a, 0.5)
    seven = lerp(cyan_a, white_a, 0.7)
    three = lerp(white_a, cyan_a, 0.7)
    segment_ramp(frame, 7, 0.0, 0.2, 0, (half, white_a), 1.0)
    segment_ramp(frame, 7, 0.2, 0.7, 0, (white_a, cyan_a), 1.0)
    segment_ramp(frame, 7, 0.7, 1.0, 0, (cyan_a, seven), 1.0)
    segment_ramp(frame, 6, 0.0, 0.3, 0, (three, cyan_a), 1.0)
    segment_ramp(frame, 6, 0.3, 0.8, 0, (cyan_a, white_a), 1.0)
    segment_ramp(frame, 6, 0.8, 1.0, 0, (white_a, three), 1.0)


def painter_c(frame, a):
    """The same span as B, but the intensity moves the fronts, not the colour.

    On the four speakers the crossover sits at 1 - a on the left and at a on the
    right, so the intensity reads as a cursor running end to end. Adds pink.
    """
    d, e = a * 0.6 + 0.2, (a + 1.0) * 0.4
    white_d, cyan_d = (d, d, d, 1.0), (0.0, d, d, 1.0)
    purple_d = (0.7 * d, 0.0, d, 1.0)
    white_e, cyan_e = (e, e, e, 1.0), (0.0, e, e, 1.0)
    pink_a, blue_a = (1.0, 0.0, 0.5, a), (0.0, 0.8, 1.0, a)

    for z in (3, 4):
        segment_lin(frame, z, 0.0, 0.5, 0, (cyan_d, white_d), 0.0, 1.0)
    for z in (3, 4):
        segment_lin(frame, z, 1.0, 0.5, 0, (purple_d, white_d), 0.0, 0.8)

    segment_lin(frame, 1, 0.0, 1.0 - a, 0, (cyan_e, white_e), 0.0, 1.0)
    segment_lin(frame, 1, 1.0 - a, 1.0, 0, (cyan_e, white_e), 1.0, 1.0)
    segment_lin(frame, 2, 1.0, a, 0, (cyan_e, white_e), 0.0, 1.0)
    segment_lin(frame, 2, a, 0.0, 0, (cyan_e, white_e), 1.0, 1.0)
    segment_lin(frame, 6, 0.0, 1.0 - a, 0, (cyan_e, white_e), 0.0, 0.9)
    segment_lin(frame, 6, 1.0 - a, 1.0, 0, (cyan_e, white_e), 1.0, 1.0)
    segment_lin(frame, 7, 1.0, a, 0, (cyan_e, white_e), 0.0, 0.9)
    segment_lin(frame, 7, a, 0.0, 0, (cyan_e, white_e), 1.0, 1.0)

    segment_lin(frame, 5, 0.0, 0.273684, 5, (BLACK, pink_a), 0.5, 1.0)
    segment_sin(frame, 5, 0.273684, 0.589474, 5, (blue_a, pink_a), 1.0, 0.0)
    segment_lin(frame, 5, 0.589474, 0.852632, 5, (BLACK, blue_a), 1.0, 0.5)
    segment_sin(frame, 5, 0.852632, 1.0, 5, (blue_a, pink_a), 0.0, 1.0)

    for z in (3, 4):
        segment_ramp(frame, z, 0.0, 1.0, 5, ((1.0, 1.0, 1.0, a), (1.0, 0.0, 0.7, a)), 1.0)


# ---------------------------------------------------------------- the strips

class Wipe:
    """Rainbow wipe that pulls back. The hue is the one piece of state a pattern
    carries across frames: it advances by a twentieth of the wheel each frame."""

    HUE_STEP = 0.05

    def __init__(self, hue=0.0):
        self.hue = hue

    def frame(self, t):
        self.hue = math.fmod(self.hue + self.HUE_STEP, 1.0)
        out = Frame()
        if t < 0.0:
            return out
        v = clamp(1.0 - t)
        r, g, b = hsv_to_rgb(self.hue, 1.0, v)
        colour = (r, g, b, 1.0)
        filled = v + 0.001
        for i, (_, _, count) in enumerate(ZONES):
            out.fill(i, colour)
            n = max(0, min(count, int(count * filled)))
            out.fill_range(i, n, count, BLACK)
        return out


def pulse_frame(t):
    """Beat: a = abs(sin(n / 120)), sampled at n = int(t * 120)."""
    a = abs(math.sin(int(t * 120.0) / 120.0))
    out = Frame()
    painter_a(out, a)
    painter_b(out, a * 0.35 + 0.2)
    return out


def fade_frame(t, speed):
    """Fade: painter A at half, painter C driven by clamp(1 - u, 0, 1).

    `speed` is 0.5 for the two-second menu cycle, 4.0 for the quarter-second
    one the game restarts on an event.
    """
    u = math.fmod(t * speed, 1.0) if speed == 0.5 else t * speed
    out = Frame()
    if u < 0.0:
        return out
    painter_a(out, 0.5)
    painter_c(out, clamp(1.0 - u) * 0.8 + 0.2)
    return out


# ----------------------------------------------------- the card reader LED

READER_FREE = 0
READER_WAITING = 1
READER_READ = 2


def reader_level(index):
    """Triangle wave over 240 frames, four seconds at 60 Hz, peaking at 255."""
    k = index % 240
    if k > 120:
        k = 240 - k
    return max(0, min(255, int(k * 2.125)))


def reader_led(index, state=READER_WAITING, mode=1, busy=False):
    """The reader LED as (r, g, b) 0..255, or None when nothing is written."""
    n = reader_level(index)
    if mode == 1:
        return {READER_FREE: (0, n, 0),
                READER_WAITING: (n, 0, 0),
                READER_READ: (0, 0, 0)}.get(state)
    if mode == 2:
        return (n, n, 0) if busy else (0, n, 255 if n >= 0x10 else 0)
    return None


# ------------------------------------------------------------- the patterns

# name -> (label, what the strips do, reader mode, reader state)
PATTERNS = {
    "off":   ("Off", "off", None, None),
    "boot":  ("Boot", "pulse frozen", 1, READER_FREE),
    "title": ("Title", "pulse", 1, READER_FREE),
    "wipe":  ("Title wipe", "rainbow wipe", 2, None),
    "card":  ("Card entry", "pulse", 1, READER_WAITING),
    "menu":  ("Song select", "2 s fade", 2, None),
    "game":  ("In game", "0.25 s fade", 2, None),
}


class Player:
    """Runs one pattern, frame by frame, the way the cabinet does.

    `frame()` returns (pixels, reader): 428 RGB floats, and the reader colour as
    (r, g, b) 0..255, or None when the pattern writes nothing to the reader.
    """

    def __init__(self):
        self.name = None
        self.elapsed = 0.0
        self.wipe = Wipe()

    def select(self, name):
        if name not in PATTERNS:
            raise ValueError(f"unknown pattern: {name}")
        if name != self.name:
            self.name = name
            self.elapsed = 0.0

    def frame(self, step=FRAME_STEP):
        name = self.name
        if name is None:
            return None, None
        t = self.elapsed
        if name == "off":
            out = Frame()
        elif name == "wipe":
            out = self.wipe.frame(t)
        elif name == "boot":
            out = pulse_frame(0.0)
        elif name in ("title", "card"):
            out = pulse_frame(t)
        elif name == "menu":
            out = fade_frame(t, 0.5)
        elif name == "game":
            out = fade_frame(t, 4.0)
        else:
            raise ValueError(f"unknown pattern: {name}")

        # The reader wave is counted in 60 Hz frames whatever the rate we play
        # at, so its four seconds stay four seconds.
        _, _, mode, state = PATTERNS[name]
        index = int(self.elapsed * 60.0)
        reader = None if mode is None else reader_led(index, state or 0, mode)
        self.elapsed += step
        return [c[:3] for c in out.leds], reader


# ---------------------------------------------------------------- the wire

def to_strips(pixels, brightness=1.0):
    """428 RGB floats -> {strip: [(r5, g5, b5), ...]}, the shape the frames take."""
    scale = clamp(brightness) * 31.0
    flat = [(min(31, int(clamp(r) * scale + 0.5)),
             min(31, int(clamp(g) * scale + 0.5)),
             min(31, int(clamp(b) * scale + 0.5))) for r, g, b in pixels]
    out, pos = {}, 0
    for s, n in enumerate(STRIP_LEDS):
        out[s] = flat[pos:pos + n]
        pos += n
    return out


if __name__ == "__main__":
    player = Player()
    for name in PATTERNS:
        player.select(name)
        pixels, reader = player.frame()
        strips = to_strips(pixels)
        lit = sum(1 for p in pixels if p != (0.0, 0.0, 0.0))
        print(f"{name:6s} {lit:3d}/428 lit  strip0[0]={strips[0][0]}  reader={reader}")
