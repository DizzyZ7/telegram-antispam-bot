"""Frozen v3 coat colours for the offline, textured plush portrait renderer.

These are colour fields, never illustrations or replacement fur.  The renderer
multiplies them by the authored neutral master and preserves its alpha, lighting,
eyes, nose and individual hairs.  All geometry uses normalized master anchors.
No global random state, files, network requests or model calls are used here.
"""
from __future__ import annotations

from hashlib import sha256
from math import pi
from types import MappingProxyType
from typing import Mapping

import numpy as np
from PIL import Image

from .core import Appearance, validate_appearance


# This table belongs to v3.  Historical v1/v2 artwork has its own frozen tables.
COLORS = MappingProxyType({
    "white": (242, 237, 226), "cream": (231, 213, 179),
    "ginger": (211, 134, 68), "silver": (172, 180, 187),
    "black": (54, 54, 57), "chocolate": (105, 71, 54),
    "blue": (119, 138, 153), "chestnut": (148, 82, 48),
    "bay": (113, 75, 51), "palomino": (213, 177, 110),
    "grey": (160, 165, 169), "brown": (137, 102, 73),
    "golden": (204, 163, 90), "red": (197, 102, 45),
    "arctic": (235, 238, 235), "tan": (183, 139, 95),
})
IVORY = (244, 235, 217)
DARK = (48, 42, 40)

_DEFAULT_ANCHORS = MappingProxyType({
    "bbox": (.14, .08, .83, .93),
    "head": (.28, .11, .77, .54),
    "body": (.28, .48, .74, .88),
    "tail": (.15, .54, .37, .87),
    "eyes": ((.47, .36), (.63, .345)),
    "nose": (.56, .41),
    "feet": ((.40, .865), (.65, .865)),
})


def _smooth(value, low: float, high: float):
    t = np.clip((value - low) / (high - low), 0, 1)
    return t * t * (3 - 2 * t)


def _rng(appearance: Appearance, namespace: str) -> np.random.Generator:
    # Palette, eyes and markings deliberately cannot move the coat pattern.
    token = (f"fosgen-pets-material-v3:{appearance.species}:"
             f"{appearance.pattern_seed}:{namespace}").encode("ascii")
    seed = int.from_bytes(sha256(token).digest()[:16], "big")
    return np.random.Generator(np.random.PCG64(seed))


def _validate(appearance: Appearance, size: tuple[int, int]) -> tuple[int, int]:
    if not isinstance(appearance, Appearance):
        raise TypeError("appearance must be an Appearance")
    # Domain validation selects the frozen v3 catalogs; extending a future
    # asset catalog must never silently extend this material version.
    validate_appearance(appearance)
    if type(appearance.asset_version) is not int or appearance.asset_version != 3:
        raise ValueError("Plush materials require asset version 3")
    if not isinstance(size, tuple) or len(size) != 2:
        raise TypeError("size must be a (width, height) tuple")
    width, height = size
    if type(width) is not int or type(height) is not int:
        raise TypeError("size dimensions must be integers")
    if not 16 <= width <= 1024 or not 16 <= height <= 1024:
        raise ValueError("material dimensions must be in 16..1024")
    return width, height


class _Context:
    """One render's small, bounded float32 surface set."""

    def __init__(self, appearance: Appearance, size: tuple[int, int], anchors):
        self.appearance = appearance
        self.width, self.height = _validate(appearance, size)
        self.x, self.y = np.meshgrid(
            np.linspace(0, 1, self.width, dtype=np.float32),
            np.linspace(0, 1, self.height, dtype=np.float32))
        self.anchors = dict(_DEFAULT_ANCHORS)
        if anchors is not None:
            if not isinstance(anchors, Mapping):
                raise TypeError("anchors must be a normalized anchor mapping")
            self.anchors.update({k: v for k, v in anchors.items()
                                 if k in self.anchors and v is not None})
        for name in ("bbox", "head", "body", "tail"):
            value = np.asarray(self.anchors[name], dtype=np.float32)
            if (value.shape != (4,) or not np.isfinite(value).all()
                    or np.any(value < 0) or np.any(value > 1)
                    or value[0] >= value[2] or value[1] >= value[3]):
                raise ValueError(f"Invalid normalized {name} anchor")
            self.anchors[name] = tuple(float(v) for v in value)
        for name, shape in (("eyes", (2, 2)), ("feet", (2, 2)), ("nose", (2,))):
            value = np.asarray(self.anchors[name], dtype=np.float32)
            if (value.shape != shape or not np.isfinite(value).all()
                    or np.any(value < 0) or np.any(value > 1)):
                raise ValueError(f"Invalid normalized {name} anchor")
            self.anchors[name] = value
        self.coarse = self.noise("coat-cloud", (6, 6))
        self.fine = self.noise("coat-edge", (27, 25))

    def noise(self, namespace: str, grid: tuple[int, int]) -> np.ndarray:
        values = _rng(self.appearance, namespace).uniform(-1, 1, grid).astype(np.float32)
        image = Image.fromarray(values).resize((self.width, self.height), Image.Resampling.BICUBIC)
        return np.clip(np.asarray(image, dtype=np.float32), -1, 1)

    def rect(self, name: str):
        return self.anchors[name]

    def region(self, name: str, margin: float = 0) -> np.ndarray:
        left, top, right, bottom = self.rect(name)
        return self.blob((left + right) / 2, (top + bottom) / 2,
                         (right - left) / 2 + margin, (bottom - top) / 2 + margin,
                         irregularity=.025, softness=.012)

    def blob(self, cx: float, cy: float, rx: float, ry: float,
             rotation: float = 0, phase: float = 0, irregularity: float = .13,
             softness: float = .007) -> np.ndarray:
        """Filled organic island with a soft fur boundary, never a ring."""
        wx = self.x - cx + self.fine * min(rx, ry) * irregularity * .46
        wy = self.y - cy + self.coarse * min(rx, ry) * irregularity * .60
        c, s = np.float32(np.cos(rotation)), np.float32(np.sin(rotation))
        dx, dy = (wx * c + wy * s) / rx, (wy * c - wx * s) / ry
        angle = np.arctan2(dy, dx)
        radius = 1 + irregularity * (
            .58 * np.sin(3 * angle + phase)
            + .31 * np.cos(5 * angle - phase * .7)
            + .11 * np.sin(7 * angle + phase * 1.4))
        distance = (radius - np.sqrt(dx * dx + dy * dy)) * min(rx, ry)
        return _smooth(distance, -softness, softness).astype(np.float32, copy=False)

    def islands(self, namespace: str, count: int, box, radii,
                *, softness: float = .007) -> np.ndarray:
        rng = _rng(self.appearance, namespace)
        left, top, right, bottom = box
        minimum, maximum = radii
        mask = np.zeros((self.height, self.width), dtype=np.float32)
        for _ in range(count):
            rx = float(rng.uniform(minimum, maximum))
            patch = self.blob(float(rng.uniform(left, right)), float(rng.uniform(top, bottom)),
                              rx, rx * float(rng.uniform(.65, 1.4)),
                              float(rng.uniform(-pi, pi)), float(rng.uniform(-pi, pi)),
                              irregularity=float(rng.uniform(.11, .26)), softness=softness)
            np.maximum(mask, patch, out=mask)
        return mask


def _paint(field: np.ndarray, color, mask: np.ndarray, strength: float = 1) -> None:
    weight = np.clip(mask * strength, 0, 1)[..., None]
    field += (np.asarray(color, dtype=np.float32) - field) * weight


def _mix(color, target, fraction: float) -> np.ndarray:
    return (np.asarray(color, dtype=np.float32) * (1 - fraction)
            + np.asarray(target, dtype=np.float32) * fraction)


def _bicolor(ctx: _Context, field, *, broken=False):
    rng = _rng(ctx.appearance, "broken" if broken else "bicolor")
    hl, ht, hr, hb = ctx.rect("head")
    bl, bt, br, bb = ctx.rect("body")
    nx, ny = ctx.anchors["nose"]
    white = ctx.blob(float(nx), float(ny + (hb - ny) * .16),
                     (hr - hl) * float(rng.uniform(.20, .35)), (hb - ht) * .20,
                     phase=float(rng.uniform(-pi, pi)))
    bib = ctx.blob((bl + br) / 2 + float(rng.uniform(-.035, .035)), bt + (bb - bt) * .39,
                   (br - bl) * float(rng.uniform(.24, .37)), (bb - bt) * .49,
                   phase=float(rng.uniform(-pi, pi)), irregularity=.22)
    np.maximum(white, bib, out=white)
    for foot in ctx.anchors["feet"]:
        np.maximum(white, ctx.blob(float(foot[0]), float(foot[1]),
                                  (br - bl) * .14, (bb - bt) * .13,
                                  phase=float(rng.uniform(-pi, pi))), out=white)
    if broken:
        # A mostly ivory rabbit with separate coloured shoulder/back islands.
        original = field.copy()
        if ctx.appearance.palette in ("white", "cream"):
            original[:] = _mix(COLORS[ctx.appearance.palette], COLORS["brown"], .58)
        field[:] = np.asarray(IVORY, dtype=np.float32)
        patches = ctx.islands("broken-islands", int(rng.integers(4, 8)),
                              (bl, ht, br, bb), (.045, .11))
        field += (original - field) * patches[..., None]
    else:
        side = ctx.islands("bicolor-extra", int(rng.integers(1, 4)),
                           (bl, bt, br, bb), (.04, .10))
        np.maximum(white, side * .88, out=white)
        _paint(field, IVORY, white)


def _tabby(ctx: _Context, field, base):
    """Curved, seed-specific forehead and flank stripes with soft boundaries."""
    rng = _rng(ctx.appearance, "tabby")
    hl, ht, hr, hb = ctx.rect("head")
    bl, bt, br, bb = ctx.rect("body")
    hw, hh, bw, bh = hr - hl, hb - ht, br - bl, bb - bt
    mask = np.zeros((ctx.height, ctx.width), dtype=np.float32)
    body_region = ctx.region("body", .012)
    # Each short brow stroke has its own bend, angle, length and width.
    eye_y = float(np.mean(ctx.anchors["eyes"][:, 1]))
    crown = ht + hh * .18
    brow_count = int(rng.integers(3, 7))
    for index in range(brow_count):
        centre = hl + hw * (.18 + .64 * (index + .5) / brow_count) + float(rng.uniform(-.035, .035))
        t = np.clip((ctx.y - crown) / max(eye_y - crown, .06), 0, 1)
        curve = centre + float(rng.uniform(-.07, .07)) * t
        curve = curve + np.sin(t * float(rng.uniform(2, 5)) + float(rng.uniform(-pi, pi))) * hw * .035
        width = hw * float(rng.uniform(.018, .038)) * (1 - .44 * t)
        stripe = 1 - _smooth(np.abs(ctx.x - curve), width * .45, width + .004)
        stripe *= _smooth(ctx.y, crown - .016, crown + .013)
        stripe *= 1 - _smooth(ctx.y, eye_y - hh * float(rng.uniform(.06, .16)), eye_y - .012)
        np.maximum(mask, stripe, out=mask)
    # Irregular curved stripes enter from each flank and taper before the belly.
    for side in (-1, 1):
        count = int(rng.integers(4, 8))
        phase = float(rng.uniform(-pi, pi))
        for index in range(count):
            height = bt + bh * (.08 + .78 * (index + .3) / count)
            inward = -(ctx.x - (bl if side < 0 else br)) / bw * side
            bend = float(rng.uniform(-.15, .14)) * bh
            curve = height + np.sin(inward * float(rng.uniform(3.2, 7)) + phase) * bh * .07
            curve += inward * bend
            width = bh * float(rng.uniform(.025, .055))
            stripe = 1 - _smooth(np.abs(ctx.y - curve), width * .35, width + .004)
            reach = float(rng.uniform(.19, .39))
            stripe *= 1 - _smooth(inward, reach * .65, reach)
            stripe *= _smooth(inward, -.08, .02)
            np.maximum(mask, stripe * body_region, out=mask)
    # A few small cheek dashes belong beside the eyes, below their protection.
    for eye in ctx.anchors["eyes"]:
        for _ in range(2):
            cheek = ctx.blob(float(eye[0] + rng.uniform(-.04, .04)),
                             float(eye[1] + rng.uniform(.025, .08)), hw * .085, hh * .014,
                             rotation=float(rng.uniform(-.4, .4)), softness=.003)
            np.maximum(mask, cheek * .65, out=mask)
    _paint(field, _mix(base, DARK, .67), mask, .88)
    # The paler chin is fur colour, not a face overlay.
    nx, ny = ctx.anchors["nose"]
    chin = ctx.blob(float(nx), float(ny + hh * .11), hw * .17, hh * .12, softness=.015)
    _paint(field, _mix(base, IVORY, .56), chin, .66)


def _patchwork(ctx: _Context, field, base, kind: str):
    rng = _rng(ctx.appearance, kind)
    left, top, right, bottom = ctx.rect("bbox")
    if kind in ("calico", "tortoiseshell"):
        # Broad mottled orange/charcoal clusters; calico adds true ivory areas.
        orange = _mix(COLORS["ginger"], base, .18)
        charcoal = _mix(DARK, base, .13)
        field[:] = charcoal
        cloud = ctx.noise(f"{kind}-orange", (8, 7))
        mask = _smooth(cloud + ctx.fine * .09, -.10, .16)
        _paint(field, orange, mask)
        if kind == "calico":
            whites = ctx.islands("calico-ivory", int(rng.integers(4, 8)),
                                  (left, top, right, bottom), (.075, .15))
            _paint(field, IVORY, whites)
    elif kind in ("pinto", "piebald"):
        # Large irregular filled islands, rather than a regular spotted pattern.
        field[:] = np.asarray(IVORY, dtype=np.float32)
        patches = ctx.islands(kind, int(rng.integers(4, 8)),
                              (left, top, right, bottom), (.085, .17))
        if ctx.appearance.palette in ("white", "cream"):
            coat = _mix(base, COLORS["brown"], .63)
        elif ctx.appearance.palette == "golden":
            coat = _mix(base, COLORS["brown"], .34)
        else:
            coat = base
        _paint(field, coat, patches)
    elif kind == "spotted":
        # Puppy/calf islands vary in count, rotation, aspect, scale and lobes.
        count = int(rng.integers(5, 13)) if ctx.appearance.species == "puppy" else int(rng.integers(5, 10))
        patches = ctx.islands("spotted-islands", count,
                              (left + .04, top + .05, right - .03, bottom - .04), (.025, .085))
        spot_color = _mix(base, IVORY, .70) if float(np.mean(base)) < 85 else _mix(base, DARK, .78)
        _paint(field, spot_color, patches)


def _foal(ctx: _Context, field, base):
    kind = ctx.appearance.pattern
    rng = _rng(ctx.appearance, f"foal-{kind}")
    bl, bt, br, bb = ctx.rect("body")
    if kind == "dapple":
        dapples = ctx.islands("dapple-filled", int(rng.integers(14, 23)),
                              (bl, bt + .02, br, bb - .015), (.012, .028), softness=.009)
        dapples *= ctx.region("body")
        _paint(field, _mix(base, IVORY, .28), dapples, .92)
    elif kind == "roan":
        # Dense tiny flecks have no coarse white islands or large hard patches.
        grain = ctx.noise("roan-ticking", (max(16, ctx.height // 2), max(16, ctx.width // 2)))
        ticking = _smooth(grain, .26, .70) * ctx.region("body", .01)
        _paint(field, _mix(base, IVORY, .55), ticking, .63)
    elif kind == "pinto":
        _patchwork(ctx, field, base, kind)
    # Bay and black points are native fur shading, not discrete decorations.
    if ctx.appearance.palette == "bay":
        for fx, fy in ctx.anchors["feet"]:
            point = ctx.blob(float(fx), float(fy - .035), .045, .085, softness=.025)
            _paint(field, _mix(base, DARK, .55), point, .85)


def _badger(ctx: _Context, field, base):
    rng = _rng(ctx.appearance, "badger-native-face")
    hl, ht, hr, hb = ctx.rect("head")
    hw, hh = hr - hl, hb - ht
    eyes, nose = ctx.anchors["eyes"], ctx.anchors["nose"]
    head = ctx.region("head", .012)
    _paint(field, _mix(base, IVORY, .74), head)
    mask = np.zeros((ctx.height, ctx.width), dtype=np.float32)
    for index, eye in enumerate(eyes):
        # Stripes run from crown through each eye to the outer lower muzzle.
        # Their seeded width/asymmetry follows anatomy instead of drawing eyes.
        t = np.clip((ctx.y - (ht + hh * .11)) / (hh * .78), 0, 1)
        eye_t = (float(eye[1]) - (ht + hh * .11)) / (hh * .78)
        drift = float(rng.uniform(-.12, .10)) * hw
        curve = float(eye[0]) + (t - eye_t) * drift
        curve += np.sin(t * pi) * float(rng.uniform(-.035, .035))
        width = hw * float(rng.uniform(.072, .125)) * (1 - .28 * t)
        if ctx.appearance.pattern == "mask":
            width *= 1.25
            curve += (index * 2 - 1) * np.sin(t * pi) * hw * .025
        stripe = 1 - _smooth(np.abs(ctx.x - curve + ctx.fine * .004), width * .65, width + .008)
        stripe *= _smooth(ctx.y, ht + hh * .07, ht + hh * .21)
        stripe *= 1 - _smooth(ctx.y, float(nose[1]) + hh * .08, hb + .01)
        np.maximum(mask, stripe, out=mask)
    _paint(field, _mix(base, DARK, .82), mask * head)
    # Preserve a recognizable narrow ivory centre line, with a seeded lean.
    centre = float(np.mean(eyes[:, 0])) + float(rng.uniform(-.018, .018))
    lean = float(rng.uniform(-.045, .045))
    width = hw * float(rng.uniform(.065, .12))
    if ctx.appearance.pattern == "mask":
        width *= .82
    blaze = 1 - _smooth(np.abs(ctx.x - centre - (ctx.y - ht) * lean), width * .7, width + .007)
    _paint(field, IVORY, blaze * head, .87)


def _raccoon(ctx: _Context, field, base):
    rng = _rng(ctx.appearance, "raccoon-native")
    hl, ht, hr, hb = ctx.rect("head")
    hw, hh = hr - hl, hb - ht
    eyes = ctx.anchors["eyes"]
    # A connected lateral eye band: native raccoon mask, not painted eyeballs.
    centre_x, centre_y = np.mean(eyes, axis=0)
    tilt = float((eyes[1, 1] - eyes[0, 1]) / max(eyes[1, 0] - eyes[0, 0], .06))
    centre_y_line = float(centre_y) + (ctx.x - float(centre_x)) * tilt
    centre_y_line += np.sin((ctx.x - hl) / hw * pi) * hh * float(rng.uniform(-.045, .045))
    band = 1 - _smooth(np.abs(ctx.y - centre_y_line), hh * .075, hh * float(rng.uniform(.14, .18)))
    band *= ctx.region("head")
    pale = ctx.blob(float(centre_x), float(centre_y + hh * .15), hw * .40, hh * .28,
                    irregularity=.05, softness=.018)
    _paint(field, _mix(base, IVORY, .70), pale)
    _paint(field, _mix(base, DARK, .87), band, .97)
    tl, tt, tr, tb = ctx.rect("tail")
    along = (ctx.y - tt) / (tb - tt) + (ctx.x - tl) / (tr - tl) * .33
    count = int(rng.integers(4, 7))
    phase = float(rng.uniform(-pi, pi))
    rings = _smooth(np.sin(along * count * 2 * pi + phase), -.13, .23)
    if ctx.appearance.pattern == "stripe":
        rings = _smooth(np.sin(along * (count + 1) * 2 * pi + phase), -.35, .17)
    _paint(field, _mix(base, DARK, .72), rings * ctx.region("tail"), .92)


def _fox(ctx: _Context, field, base):
    if ctx.appearance.palette == "arctic":
        # Arctic solids are naturally white; optional points remain quiet grey.
        if ctx.appearance.pattern == "socks":
            for fx, fy in ctx.anchors["feet"]:
                _paint(field, _mix(base, COLORS["silver"], .34),
                       ctx.blob(float(fx), float(fy - .015), .044, .070, softness=.015))
        elif ctx.appearance.pattern == "mask":
            for ex, ey in ctx.anchors["eyes"]:
                _paint(field, _mix(base, COLORS["silver"], .28),
                       ctx.blob(float(ex), float(ey), .055, .035, softness=.019))
        return
    rng = _rng(ctx.appearance, "fox-native")
    hl, ht, hr, hb = ctx.rect("head")
    bl, bt, br, bb = ctx.rect("body")
    nx, ny = ctx.anchors["nose"]
    white = np.zeros((ctx.height, ctx.width), dtype=np.float32)
    for eye in ctx.anchors["eyes"]:
        cheek = ctx.blob(float(eye[0]), float(ny + (hb - ny) * .2),
                         (hr - hl) * .25, (hb - ht) * .15,
                         rotation=float(rng.uniform(-.17, .17)), softness=.012)
        np.maximum(white, cheek, out=white)
    bib = ctx.blob(float(nx), bt + (bb - bt) * .28, (br - bl) * .28,
                   (bb - bt) * .40, phase=float(rng.uniform(-pi, pi)), softness=.013)
    np.maximum(white, bib, out=white)
    tl, tt, tr, tb = ctx.rect("tail")
    # The seated master curls its tail downward and forward: the free tip is
    # at the lower inner edge, rather than the upper edge beside its shoulder.
    tail_tip = ctx.blob(tl + (tr - tl) * .22, tt + (tb - tt) * .82,
                        (tr - tl) * .36, (tb - tt) * .27, softness=.012)
    np.maximum(white, tail_tip * ctx.region("tail", .01), out=white)
    _paint(field, IVORY, white)
    if ctx.appearance.pattern == "socks":
        for fx, fy in ctx.anchors["feet"]:
            sock = ctx.blob(float(fx), float(fy - .015), .044, .070,
                            phase=float(rng.uniform(-pi, pi)), softness=.013)
            _paint(field, _mix(base, DARK, .80), sock)
    elif ctx.appearance.pattern == "mask":
        for eye in ctx.anchors["eyes"]:
            patch = ctx.blob(float(eye[0]), float(eye[1]), (hr - hl) * .17,
                             (hb - ht) * .10, softness=.012)
            _paint(field, _mix(base, DARK, .48), patch, .75)


def color_field(appearance: Appearance, size: tuple[int, int] = (512, 512),
                *, anchors: dict | None = None) -> np.ndarray:
    """Return an H×W×3 float32 coat albedo in 0..255, without face/alpha edits.

    ``size`` is (width, height).  Rectangles in ``anchors`` are (left, top,
    right, bottom); points and rectangles use normalized 0..1 coordinates.
    The returned array is owned by the caller and has no shared mutable state.
    """
    ctx = _Context(appearance, size, anchors)
    species, kind = appearance.species, appearance.pattern
    base = np.asarray(COLORS[appearance.palette], dtype=np.float32)
    # Subtle diffuse albedo variation; all fine fur and directional light comes
    # from the master.  A solid capybara/otter never receives invented patches.
    shade = (1 + ctx.coarse * .018 + ctx.fine * .004).astype(np.float32)
    field = (shade[..., None] * base).astype(np.float32)

    if species == "badger":
        _badger(ctx, field, base)
    elif species == "raccoon":
        _raccoon(ctx, field, base)
    elif species == "fox":
        _fox(ctx, field, base)
    elif species == "foal":
        _foal(ctx, field, base)
    elif kind == "tabby":
        _tabby(ctx, field, base)
    elif kind in ("calico", "tortoiseshell", "piebald"):
        _patchwork(ctx, field, base, kind)
    elif kind in ("bicolor", "broken"):
        _bicolor(ctx, field, broken=kind == "broken")
    elif kind == "spotted" and species == "fawn":
        bl, bt, br, bb = ctx.rect("body")
        rng = _rng(appearance, "fawn-flecks")
        spots = np.zeros((ctx.height, ctx.width), dtype=np.float32)
        for side in (0, 1):
            box = (bl + (br - bl) * (.04 if side == 0 else .67), bt + .025,
                   bl + (br - bl) * (.33 if side == 0 else .96), bb - .055)
            flecks = ctx.islands(f"fawn-flank-{side}", int(rng.integers(10, 17)),
                                 box, (.006, .011), softness=.0025)
            np.maximum(spots, flecks, out=spots)
        _paint(field, IVORY, spots * ctx.region("body"), .92)
    elif kind == "spotted":
        _patchwork(ctx, field, base, kind)
    elif kind == "sable":
        hl, ht, hr, hb = ctx.rect("head")
        crown = 1 - _smooth(ctx.y, ht + (hb - ht) * .25, ht + (hb - ht) * .72)
        back = _smooth(ctx.coarse + (ctx.x - .5) * .35, -.2, .50) * ctx.region("body")
        _paint(field, _mix(base, DARK, .68), np.maximum(crown * ctx.region("head"), back), .71)
    elif kind == "pointed":
        hl, ht, hr, hb = ctx.rect("head")
        nx, ny = ctx.anchors["nose"]
        points = ctx.blob(float(nx), float(ny), (hr - hl) * .20, (hb - ht) * .16,
                          softness=.025)
        for fx, fy in ctx.anchors["feet"]:
            np.maximum(points, ctx.blob(float(fx), float(fy), .048, .054, softness=.021), out=points)
        # Both upright ears receive broad, softly graded dark tips.
        tips = (1 - _smooth(ctx.y, ht + (hb - ht) * .16, ht + (hb - ht) * .35))
        tips *= _smooth(np.abs(ctx.x - (hl + hr) / 2), (hr - hl) * .12, (hr - hl) * .24)
        np.maximum(points, tips, out=points)
        _paint(field, _mix(base, DARK, .73), points, .88)
    elif kind == "shaded":
        # A gentle, continuous dorsal darkening, never a patchy capybara coat.
        bl, bt, br, bb = ctx.rect("body")
        dorsal = (1 - _smooth(ctx.y, bt, bb)) * .15
        _paint(field, _mix(base, DARK, .55), dorsal)
    elif kind == "bib":
        bl, bt, br, bb = ctx.rect("body")
        bib = ctx.blob((bl + br) / 2, bt + (bb - bt) * .18,
                       (br - bl) * .26, (bb - bt) * .29, softness=.021)
        _paint(field, _mix(base, IVORY, .76), bib, .84)

    np.clip(field, 0, 255, out=field)
    return np.ascontiguousarray(field, dtype=np.float32)


def marking_mask(appearance: Appearance, size: tuple[int, int] = (512, 512),
                 *, anchors: dict | None = None) -> np.ndarray:
    """Return an H×W float32 anatomical marking mask, with canonical variants.

    The caller applies a contrasting natural fur material with the master's
    texture and protects face details. Marks remain tiny and anatomical.
    """
    ctx = _Context(appearance, size, anchors)
    kind, variant = appearance.marking, appearance.marking_variant
    if kind == "none":
        return np.zeros((ctx.height, ctx.width), dtype=np.float32)
    hl, ht, hr, hb = ctx.rect("head")
    hw, hh = hr - hl, hb - ht
    eyes, nose = ctx.anchors["eyes"], ctx.anchors["nose"]
    eye_y = float(np.mean(eyes[:, 1]))
    centre = float(np.mean(eyes[:, 0]))
    # Five sizes × seven slight placements = all 35 canonical variants.
    placement, scale_step = variant % 7, variant // 7
    scale = .82 + scale_step * .10
    cx = centre + (placement - 3) * hw * .016
    cy = ht + (eye_y - ht) * (.48 + (placement % 3 - 1) * .035)

    if kind in ("sock_left", "sock_right"):
        foot = ctx.anchors["feet"][0 if kind == "sock_left" else 1]
        column, row = variant % 3, variant // 3
        rx = hw * (.071 + column * .009)
        ry = hh * (.064 + row * .013)
        return ctx.blob(float(foot[0]), float(foot[1] - ry * .30), rx, ry,
                        phase=variant * .57, irregularity=.075, softness=.006)
    if kind == "freckle":
        # Four small placements near the muzzle, always clear of both eyes.
        side = -1 if variant % 2 == 0 else 1
        cx = float(nose[0]) + side * hw * (.11 if variant < 2 else .16)
        cy = float(nose[1]) + hh * (.038 if variant < 2 else .07)
        return ctx.blob(cx, cy, hw * .018, hh * .017,
                        phase=variant * .9, softness=.002)
    if kind == "blaze":
        y_start, y_end = cy - hh * .12, min(float(nose[1]) - hh * .06, eye_y + hh * .15)
        t = np.clip((ctx.y - y_start) / max(y_end - y_start, .01), 0, 1)
        lean = (placement - 3) * hw * .011 * t
        width = hw * (.019 + scale_step * .004) * (1 - .48 * t)
        width *= 1 + ctx.fine * .14
        mask = 1 - _smooth(np.abs(ctx.x - cx - lean), width * .55, width + .004)
        mask *= _smooth(ctx.y, y_start - .005, y_start + .01)
        mask *= 1 - _smooth(ctx.y, y_end - .012, y_end + .003)
        return mask.astype(np.float32, copy=False)

    # Smooth radial star/heart silhouettes follow hair boundaries and are only
    # a few percent of head width.  They are masks for fur, not vector artwork.
    dx, dy = (ctx.x - cx) / (hw * .047 * scale), (ctx.y - cy) / (hh * .055 * scale)
    if kind == "star":
        angle = np.arctan2(dy, dx) + pi / 2 + (placement - 3) * .035
        radius = .76 + .23 * np.cos(5 * angle)
        signed = radius - np.sqrt(dx * dx + dy * dy)
    else:  # heart, with a shallow furry cleft and rounded lobes.
        angle = np.arctan2(dy, dx)
        radius = .80 - .21 * np.sin(angle) + .16 * np.cos(2 * angle)
        radius -= .31 * np.exp(-((angle + pi / 2) / .32) ** 2)
        signed = radius - np.sqrt(dx * dx + dy * dy)
    mask = _smooth(signed + ctx.fine * .045, -.13, .13)
    return mask.astype(np.float32, copy=False)


__all__ = ["COLORS", "IVORY", "color_field", "marking_mask"]
