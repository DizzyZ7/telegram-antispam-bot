"""Small, CPU-bounded raster diagnostics for the offline art review.

Only PNG byte strings are accepted: DNA never supplies paths, URLs or SVG input.
The 64-bit pHash uses a 32x32 luminance DCT; the DC bit is always zero. SSIM is
the mean of population-statistic SSIM over nonoverlapping 8x8 luminance windows,
with edge replication for partial windows, L=255, K1=.01 and K2=.03. This is an
explicit, reproducible diagnostic variant, not a claim of skimage equivalence.

RGB hashes and differences use visible pixels composited onto white. Neither
these metrics nor a finite gallery prove global uniqueness or artistic quality.
"""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
from io import BytesIO

import numpy as np
from PIL import Image, UnidentifiedImageError

MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_DIMENSION = 2048
CHANGED_CHANNEL_THRESHOLD = 8
_LUMA = np.array((.2126, .7152, .0722), dtype=np.float64)


def _decode(png: bytes) -> tuple[np.ndarray, np.ndarray]:
    if not isinstance(png, (bytes, bytearray, memoryview)):
        raise TypeError("Metrics accept PNG bytes, never paths or URLs")
    if not 0 < len(png) <= MAX_PNG_BYTES:
        raise ValueError("PNG byte size exceeds metric limits")
    try:
        with Image.open(BytesIO(png)) as image:
            if image.format != "PNG":
                raise ValueError("Only PNG images are supported")
            if min(image.size) < 1 or max(image.size) > MAX_DIMENSION:
                raise ValueError("PNG dimensions exceed metric limits")
            rgba = image.convert("RGBA")
            alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            rgb = np.asarray(Image.alpha_composite(background, rgba).convert("RGB"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Invalid or oversized PNG image") from error
    return rgb, alpha


def _luminance(rgb: np.ndarray) -> np.ndarray:
    return rgb @ _LUMA


@lru_cache(maxsize=1)
def _dct_matrix() -> np.ndarray:
    # Orthonormal DCT-II, written directly to avoid a scipy dependency.
    frequencies = np.arange(32, dtype=np.float64)[:, None]
    positions = np.arange(32, dtype=np.float64)[None, :] + .5
    matrix = np.cos(np.pi * frequencies * positions / 32) * np.sqrt(2 / 32)
    matrix[0] /= np.sqrt(2)
    matrix.flags.writeable = False
    return matrix


def _phash(rgb: np.ndarray) -> str:
    reduced = np.asarray(Image.fromarray(rgb).resize((32, 32), Image.Resampling.LANCZOS),
                         dtype=np.uint8)
    coefficients = (_dct_matrix() @ _luminance(reduced) @ _dct_matrix().T)[:8, :8].copy()
    # Remove floating point residue for constant colors and symmetric fixtures.
    coefficients[np.abs(coefficients) < 1e-8] = 0
    threshold = float(np.median(coefficients.ravel()[1:]))
    bits = coefficients.ravel() > threshold
    bits[0] = False
    value = 0
    for bit in bits:
        value = (value << 1) | bool(bit)
    return f"{value:016x}"


def _foreground(rgb: np.ndarray, alpha: np.ndarray) -> tuple[list[int], str]:
    if np.any(alpha < 250):
        mask = alpha > 8
        method = "alpha>8"
    else:
        # An estimate only: corner colors approximate the studio background.
        border = max(1, min(rgb.shape[:2]) // 32)
        corners = np.concatenate((rgb[:border, :border].reshape(-1, 3),
                                  rgb[:border, -border:].reshape(-1, 3),
                                  rgb[-border:, :border].reshape(-1, 3),
                                  rgb[-border:, -border:].reshape(-1, 3)))
        background = np.median(corners, axis=0)
        mask = np.max(np.abs(rgb.astype(np.int16) - background), axis=2) > 18
        method = "corner-background-distance>18 (estimated)"
    ys, xs = np.nonzero(mask)
    if not xs.size:
        return [0, 0, rgb.shape[1], rgb.shape[0]], method + "; empty uses full frame"
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], method


def _summary(rgb: np.ndarray, alpha: np.ndarray) -> dict:
    bbox, method = _foreground(rgb, alpha)
    x0, y0, x1, y1 = bbox
    luminance = _luminance(rgb[y0:y1, x0:x1])
    histogram = np.histogram(luminance, bins=16, range=(0, 256))[0]
    histogram = histogram / max(1, histogram.sum())
    return {
        "foreground_bbox": bbox,
        "foreground_method": method,
        "foreground_luminance_mean": float(luminance.mean()),
        "foreground_luminance_stddev": float(luminance.std()),
        "foreground_luminance_contrast": float(np.percentile(luminance, 95)
                                                 - np.percentile(luminance, 5)),
        "foreground_luminance_histogram": histogram.tolist(),
    }


def image_metrics(png: bytes) -> dict:
    """Return dimensions, file/RGB SHA256, pHash and estimated foreground data."""
    rgb, alpha = _decode(png)
    return {
        "width": int(rgb.shape[1]), "height": int(rgb.shape[0]),
        "file_sha256": sha256(png).hexdigest(),
        "rgb_sha256": sha256(rgb.tobytes()).hexdigest(),
        "phash": _phash(rgb),
        **_summary(rgb, alpha),
    }


def _ssim(first: np.ndarray, second: np.ndarray) -> float:
    def windows(luminance):
        height, width = luminance.shape
        padded = np.pad(luminance, ((0, -height % 8), (0, -width % 8)), mode="edge")
        return padded.reshape(padded.shape[0] // 8, 8, padded.shape[1] // 8, 8).transpose(0, 2, 1, 3)
    x, y = windows(first), windows(second)
    mx, my = x.mean(axis=(2, 3)), y.mean(axis=(2, 3))
    dx, dy = x - mx[..., None, None], y - my[..., None, None]
    vx, vy = (dx * dx).mean(axis=(2, 3)), (dy * dy).mean(axis=(2, 3))
    covariance = (dx * dy).mean(axis=(2, 3))
    c1, c2 = (.01 * 255) ** 2, (.03 * 255) ** 2
    scores = ((2 * mx * my + c1) * (2 * covariance + c2)
              / ((mx * mx + my * my + c1) * (vx + vy + c2)))
    return float(np.clip(scores.mean(), -1, 1))


def _diagnostic(*, identical: bool, changed: float, ssim: float,
                species: str | None, pattern: str | None) -> dict:
    # Species with obligatory masks/stripes naturally preserve more structure.
    # Solids may be intentionally very similar and never fail an admission gate.
    if pattern == "solid":
        minimum, maximum = 0.0, 1.0
    elif pattern in {"shaded", "bib", "socks", "pointed"}:
        minimum, maximum = .003, .995
    elif species in {"badger", "raccoon"}:
        minimum, maximum = .004, .995
    elif pattern in {"tabby", "tortoiseshell", "calico", "spotted", "dapple"}:
        minimum, maximum = .012, .985
    else:
        minimum, maximum = .008, .990
    similar = changed < minimum or ssim > maximum
    reasons = []
    if identical:
        reasons.append("Identical visible RGB pixels in this sampled pair")
    elif similar:
        reasons.append("Patterned pair is close; inspect the actual portraits")
    elif pattern == "solid":
        reasons.append("High similarity is permitted for solid coats")
    return {
        "species": species, "pattern": pattern,
        "minimum_changed_pixel_ratio": minimum,
        "similarity_ssim_threshold": maximum,
        "review_level": "review" if identical or similar else "info",
        "reasons": reasons,
        "scope": "Informational sampled-pair review; no admission or global uniqueness verdict",
    }


def compare_images(first_png: bytes, second_png: bytes, *,
                   species: str | None = None, pattern: str | None = None) -> dict:
    """Compare same-size actual PNGs; optional genes tune informational flags."""
    first, first_alpha = _decode(first_png)
    second, second_alpha = _decode(second_png)
    if first.shape != second.shape:
        raise ValueError("Comparison requires equal image dimensions")
    difference = np.abs(first.astype(np.int16) - second.astype(np.int16))
    changed = float(np.mean(difference.max(axis=2) > CHANGED_CHANNEL_THRESHOLD))
    first_luma, second_luma = _luminance(first), _luminance(second)
    ssim = _ssim(first_luma, second_luma)
    first_bbox, _ = _foreground(first, first_alpha)
    second_bbox, _ = _foreground(second, second_alpha)
    x0, y0 = min(first_bbox[0], second_bbox[0]), min(first_bbox[1], second_bbox[1])
    x1, y1 = max(first_bbox[2], second_bbox[2]), max(first_bbox[3], second_bbox[3])
    identical = bool(np.array_equal(first, second))
    first_phash, second_phash = _phash(first), _phash(second)
    return {
        "identical_rgb": identical,
        "first_rgb_sha256": sha256(first.tobytes()).hexdigest(),
        "second_rgb_sha256": sha256(second.tobytes()).hexdigest(),
        "first_phash": first_phash, "second_phash": second_phash,
        "phash_distance": (int(first_phash, 16) ^ int(second_phash, 16)).bit_count(),
        "ssim": ssim,
        "ssim_method": "mean nonoverlapping 8x8 luminance windows, population covariance, L=255",
        "foreground_ssim": _ssim(first_luma[y0:y1, x0:x1], second_luma[y0:y1, x0:x1]),
        "foreground_comparison_bbox": [x0, y0, x1, y1],
        "changed_pixel_ratio": changed,
        "changed_channel_threshold": CHANGED_CHANNEL_THRESHOLD,
        "mean_absolute_rgb_difference": float(difference.mean()),
        "foreground_mean_absolute_rgb_difference": float(difference[y0:y1, x0:x1].mean()),
        "mean_absolute_luminance_difference": float(np.abs(first_luma - second_luma).mean()),
        "diagnostic": _diagnostic(identical=identical, changed=changed, ssim=ssim,
                                  species=species, pattern=pattern),
    }
