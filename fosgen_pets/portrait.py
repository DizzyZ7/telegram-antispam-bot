"""Bounded, self-contained rasterization of validated versioned art."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import threading

from PIL import Image

from .core import Appearance
from .render_svg import render_svg

_RENDER_SLOTS = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class Portrait:
    svg: str
    png: bytes
    pixel_hash: str
    renderer_version: str


def render_portrait(appearance: Appearance) -> Portrait:
    """No external SVG input, URLs, fonts, files or caller-controlled dimensions."""
    import cairosvg

    with _RENDER_SLOTS:
        svg = render_svg(appearance)
        png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=512, output_height=512, unsafe=False)
        with Image.open(BytesIO(png)) as image:
            pixel_hash = sha256(image.convert("RGBA").tobytes()).hexdigest()
        return Portrait(svg, png, pixel_hash, f"fosgen-svg-v{appearance.asset_version}")


def render_png(appearance: Appearance) -> bytes:
    return render_portrait(appearance).png
