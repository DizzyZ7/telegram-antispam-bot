"""Original offline plush art with local, bounded, deterministic customization.

No network/model/GPU and no untrusted SVG enters this renderer. Neutral masters
supply real fur, anatomical volume and glass-eye highlights. Colour fields are
multiplied by that material, then warped together with every anatomical mask.
"""
from dataclasses import dataclass
import base64
from hashlib import sha256
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .core import Appearance, validate_appearance
from .plush_assets import load_master, normalized_anchors
from .plush_materials import IVORY, color_field, marking_mask

SIZE = 512
_EYES = {
    "brown": (100, 63, 36), "amber": (151, 105, 40), "hazel": (109, 105, 55),
    "green": (67, 112, 75), "blue": (66, 110, 142),
}


@dataclass(frozen=True)
class RenderLayers:
    image: Image.Image
    subject: Image.Image
    silhouette: Image.Image
    fur: Image.Image
    eyes: Image.Image
    markings: Image.Image


def _soft_ellipses(boxes: list) -> np.ndarray:
    mask = Image.new("L", (SIZE, SIZE))
    draw = ImageDraw.Draw(mask)
    for box in boxes:
        draw.ellipse(tuple(box), fill=255)
    return np.asarray(mask.filter(ImageFilter.GaussianBlur(1.1)), dtype=np.float32) / 255


def _traits(appearance: Appearance) -> tuple:
    digest = sha256(f"fosgen-plush-anatomy-v3:{appearance.species}:{appearance.pattern_seed}".encode()).digest()
    return (int.from_bytes(digest[:2], "big") / 65535,
            int.from_bytes(digest[2:4], "big") / 65535,
            int.from_bytes(digest[4:6], "big") / 65535)


def _mesh(appearance: Appearance, record: dict) -> list:
    width, asymmetry, cheeks = _traits(appearance)
    left, top, right, bottom = record["head"]
    ex = sum((b[0] + b[2]) / 2 for b in record["eye_boxes"]) / 2
    ey = sum((b[1] + b[3]) / 2 for b in record["eye_boxes"]) / 2
    spread = (right - left) * .48
    strength = (width - .5) * .065

    def source(x, y):
        weight = float(np.exp(-((y - ey) / max(30, (bottom - top) * .70)) ** 4))
        side = float(np.exp(-((x - ex) / max(30, spread)) ** 4))
        # The whole colour/eye/alpha stack shares the same gentle deformation.
        sx = ex + (x - ex) / (1 + strength * weight)
        sx += (asymmetry - .5) * 2.4 * weight * side
        sy = y + (cheeks - .5) * 3.2 * weight * side
        return sx, sy

    result = []
    for top_y in range(0, SIZE, 64):
        for left_x in range(0, SIZE, 64):
            right_x, bottom_y = left_x + 64, top_y + 64
            quad = (*source(left_x, top_y), *source(left_x, bottom_y),
                    *source(right_x, bottom_y), *source(right_x, top_y))
            result.append(((left_x, top_y, right_x, bottom_y), quad))
    return result


def _warp(image: Image.Image, mesh) -> Image.Image:
    return image.transform((SIZE, SIZE), Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)


def _as_mask(values: np.ndarray) -> Image.Image:
    return Image.fromarray(np.rint(np.clip(values, 0, 1) * 255).astype(np.uint8))


def _eye_tint(source: np.ndarray, eye_masks: np.ndarray, appearance: Appearance) -> np.ndarray:
    gray = source @ np.asarray((.2126, .7152, .0722), dtype=np.float32)
    warm = np.clip((source[..., 0] - source[..., 2] - 3) / 20, 0, 1)
    # Bright catchlights and black pupils stay original; tint only visible iris fibres.
    iris = eye_masks * warm * (gray > 22) * (gray < 170)
    target = np.asarray(_EYES[appearance.eyes], dtype=np.float32)
    hue, _, softness = _traits(appearance)
    target *= np.asarray((.96 + hue * .08, .98 + softness * .04, 1.04 - hue * .08), dtype=np.float32)
    target /= float(target @ np.asarray((.2126, .7152, .0722), dtype=np.float32))
    tinted = gray[..., None] * target
    return source + (tinted - source) * (iris[..., None] * .85)


def render_layers(appearance: Appearance) -> RenderLayers:
    validate_appearance(appearance)
    if appearance.asset_version != 3:
        raise ValueError("Plush rendering requires asset_version=3")
    master, record = load_master(appearance.species)
    anchors = normalized_anchors(record)
    original = np.asarray(master, dtype=np.float32)
    source = original[..., :3]
    alpha = original[..., 3] / 255
    gray = source @ np.asarray((.2126, .7152, .0722), dtype=np.float32)
    eyes = _soft_ellipses(record["eye_boxes"])
    noses = _soft_ellipses(record["nose_boxes"])
    # Preserve the dark nose/nostrils, not the ivory fur inside their guide box.
    # The latter otherwise creates pale halos on black/bay ungulates.
    noses *= np.clip((190 - gray) / 70, 0, 1)
    protected = np.maximum(eyes, noses)
    fur = alpha * (1 - protected)
    material = color_field(appearance, anchors=anchors)
    # The coat includes ears, but a forehead marking belongs on the face, never
    # in the gap between a rabbit's ears or on an alpaca's ear tips.
    mark_anchors = dict(anchors)
    mark_anchors["head"] = tuple(value / SIZE for value in record.get("face", record["head"]))
    mark = marking_mask(appearance, anchors=mark_anchors) * (1 - protected)
    # Ivory-on-ivory would erase a legitimate freckle or star entirely. Keep a
    # restrained warm accent on light coats, cream on dark coats. This choice
    # belongs to the frozen v3 material, not a user-selected drawing colour.
    light_coat = (material @ np.asarray((.2126, .7152, .0722), dtype=np.float32)) > 195
    mark_color = np.where(light_coat[..., None],
                          np.asarray((186, 157, 121), dtype=np.float32),
                          np.asarray(IVORY, dtype=np.float32))
    material += (mark_color - material) * mark[..., None]
    # Preserve every hair and the source's real light/shadow structure. No flat paint.
    shade = np.clip(source / np.asarray((239, 230, 212), dtype=np.float32), .08, 1.18)
    rgb = material * shade
    # A little reflected studio light remains visible on dark velvet as well.
    reflection = np.clip((gray - 222) / 30, 0, 1) * 5
    rgb += reflection[..., None]
    feature_source = _eye_tint(source, eyes, appearance)
    rgb += (feature_source - rgb) * protected[..., None]
    rgba = np.concatenate((np.rint(np.clip(rgb, 0, 255)), original[..., 3:]), axis=2).astype(np.uint8)
    mesh = _mesh(appearance, record)
    subject = _warp(Image.fromarray(rgba), mesh)
    silhouette = subject.getchannel("A")
    warped_alpha = np.asarray(silhouette, dtype=np.float32) / 255

    def mask(values):
        result = np.asarray(_warp(_as_mask(values), mesh), dtype=np.float32) / 255
        return _as_mask(result * warped_alpha)

    fur_layer = mask(fur)
    eye_layer = mask(eyes)
    mark_layer = mask(mark)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    radius = np.clip(((xx - 255) / 430) ** 2 + ((yy - 210) / 500) ** 2, 0, 1)
    backdrop = np.stack((253 - radius * 5, 250 - radius * 7, 244 - radius * 7), axis=2)
    background = Image.fromarray(np.rint(backdrop).astype(np.uint8)).convert("RGBA")
    box = record["bbox"]
    shadow = Image.new("RGBA", (SIZE, SIZE))
    draw = ImageDraw.Draw(shadow)
    center_x, baseline = (box[0] + box[2]) / 2, box[3] + 2
    half = (box[2] - box[0]) * .39
    draw.ellipse((center_x - half, baseline - 9, center_x + half, baseline + 8), fill=(112, 87, 60, 54))
    background.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(7)))
    background.alpha_composite(subject)
    return RenderLayers(background.convert("RGB"), subject, silhouette, fur_layer, eye_layer, mark_layer)


def render_v3(appearance: Appearance) -> tuple[str, bytes]:
    layers = render_layers(appearance)
    buffer = BytesIO()
    layers.image.save(buffer, format="PNG", compress_level=6)
    png = buffer.getvalue()
    encoded = base64.b64encode(png).decode("ascii")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512" data-asset-version="3">'
           '<image width="512" height="512" href="data:image/png;base64,' + encoded + '"/></svg>')
    return svg, png
