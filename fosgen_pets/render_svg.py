"""Deterministic, masked SVG proof-of-concept for three Fosgen Pets species.

Every drawing uses one fixed 512x512 coordinate system. Color/pattern elements
are clipped to body/head masks; face and outlines are drawn last. This is a
technical prototype, not final commissioned storybook art.
"""
from __future__ import annotations

from hashlib import sha256
from html import escape
from .core import Appearance, PALETTES, PATTERNS

SUPPORTED = frozenset(("kitten", "foal", "calf"))
COLORS = {
    "cream": "#f7e7c9", "ginger": "#e9a15f", "silver": "#c3c5d2",
    "black": "#53505d", "chocolate": "#956552", "blue": "#929cba",
    "chestnut": "#b87652", "bay": "#996247", "palomino": "#e9c48b",
    "grey": "#c9c6c4", "brown": "#b58a71", "golden": "#dfb77d",
}
EYES = {
    "brown": "#6b493c", "amber": "#ba7b38", "hazel": "#7e8550",
    "green": "#6f9c73", "blue": "#709fc3",
}
# Shapes are authored in shared art coordinates, not resized per marking.
GEOMETRY = {
    "kitten": {
        "body": "M125 293 Q106 244 160 223 Q257 187 343 235 Q395 275 368 368 Q338 412 203 400 Q125 384 125 293Z",
        "head": "M148 225 L136 113 Q143 91 170 111 L218 149 Q258 136 294 149 L341 109 Q368 95 368 123 L359 231 Q356 299 253 308 Q153 307 148 225Z",
        "ears": '<path d="M149 190 L148 121 L207 163Z M309 163 L354 120 L351 190Z" fill="#f3b6b9"/>',
        "extras": '<path d="M363 337 Q444 361 426 291 Q413 271 399 288" fill="none" stroke="{base}" stroke-width="28" stroke-linecap="round"/>',
    },
    "foal": {
        "body": "M122 281 Q135 227 214 220 L335 233 Q385 263 368 349 L347 385 L322 385 L313 348 L196 351 L188 394 L159 394 L146 342 Q117 329 122 281Z",
        "head": "M184 206 L171 125 Q180 105 196 123 L216 157 L251 144 L282 113 Q300 103 300 130 L293 178 Q328 215 306 272 Q291 305 225 307 Q162 302 155 262 Q154 224 184 206Z",
        "ears": '<path d="M186 169 L181 131 L206 163Z M265 157 L290 130 L286 177Z" fill="#f0b5b4"/>',
        "extras": '<path d="M350 248 Q414 260 409 322" fill="none" stroke="{base}" stroke-width="20" stroke-linecap="round"/><path d="M209 154 Q223 122 243 151 L263 161" fill="none" stroke="#f8e9ce" stroke-width="22" stroke-linecap="round"/>',
    },
    "calf": {
        "body": "M115 289 Q110 231 180 216 Q266 194 349 236 Q393 272 365 365 Q342 403 197 397 Q121 384 115 289Z",
        "head": "M157 214 Q137 187 126 157 Q119 139 146 147 L186 169 Q249 139 306 168 L355 143 Q381 140 373 166 Q365 190 345 214 Q366 283 297 314 Q212 346 155 290 Q139 260 157 214Z",
        "ears": '<ellipse cx="140" cy="180" rx="35" ry="19" fill="#eeb3ae"/><ellipse cx="356" cy="180" rx="35" ry="19" fill="#eeb3ae"/>',
        "extras": '<ellipse cx="252" cy="282" rx="72" ry="42" fill="#f1b9b4"/><ellipse cx="228" cy="281" rx="8" ry="6" fill="#c98b88"/><ellipse cx="278" cy="281" rx="8" ry="6" fill="#c98b88"/>',
    },
}


def render_svg(appearance: Appearance) -> str:
    """Render an immutable appearance as a complete SVG document.

    All user-supplied appearance fields are validated against finite catalogs.
    Marking variants affect actual geometry, not just metadata.
    """
    if appearance.species not in SUPPORTED:
        raise ValueError("No art template for species")
    if appearance.palette not in PALETTES[appearance.species] or appearance.palette not in COLORS:
        raise ValueError("Invalid palette")
    if appearance.pattern not in PATTERNS[appearance.species]:
        raise ValueError("Invalid pattern")
    if appearance.eyes not in EYES or appearance.marking not in (
        "none", "star", "heart", "blaze", "freckle", "sock_left", "sock_right"
    ):
        raise ValueError("Invalid feature")
    if not 0 <= appearance.marking_variant < 4096 or appearance.asset_version != 1:
        raise ValueError("Invalid asset version or marking variant")
    geom = GEOMETRY[appearance.species]
    base = COLORS[appearance.palette]
    # Every variant changes at least one visible geometric coordinate.
    variant = appearance.marking_variant
    x = 179 + (variant % 103)
    y = 237 + ((variant // 103) % 33)
    radius = 11 + (variant % 13)
    pattern_shapes = ""
    if appearance.pattern != "solid":
        digest = sha256(appearance.canonical_dna().encode()).digest()
        blobs = []
        for i in range(8):
            px = 150 + digest[i] % 210
            py = 201 + digest[i + 8] % 158
            r = 12 + digest[i + 16] % 21
            blobs.append(f'<ellipse cx="{px}" cy="{py}" rx="{r}" ry="{r*3//4}"/>')
        pattern_shapes = f'<g fill="#fff8ed" opacity=".8">{"".join(blobs)}</g>'
    marks = {
        "none": "",
        "star": f'<path d="M{x} {y-radius} l{radius//3} {radius} h{radius} l-{radius*4//5} {radius//2} l{radius//3} {radius} l-{radius} -{radius//2} l-{radius} {radius//2} l{radius//3} -{radius} l-{radius*4//5} -{radius//2} h{radius}Z" fill="#fff9ed"/>',
        "heart": f'<path d="M{x} {y+radius} C{x-radius*2} {y-radius//2} {x-radius} {y-radius*2} {x} {y-radius//2} C{x+radius} {y-radius*2} {x+radius*2} {y-radius//2} {x} {y+radius}Z" fill="#fff9ed"/>',
        "blaze": f'<path d="M{x-6} {y-24} Q{x+radius} {y} {x+2} {y+35} L{x-12} {y+27}Z" fill="#fff9ed"/>',
        "freckle": f'<circle cx="{x}" cy="{y}" r="5" fill="#9e6b66"/><circle cx="{x+18}" cy="{y+9}" r="4" fill="#9e6b66"/>',
        "sock_left": f'<ellipse cx="{x}" cy="376" rx="{radius}" ry="15" fill="#fff9ed"/>',
        "sock_right": f'<ellipse cx="{x+55}" cy="376" rx="{radius}" ry="15" fill="#fff9ed"/>',
    }
    # The forehead/body masks protect patterns from bleeding beyond silhouettes.
    shape = geom["body"] + " " + geom["head"]
    extras = geom["extras"].replace("{base}", base)
    eyes = EYES[appearance.eyes]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">'
        '<defs><clipPath id="fur"><path d="' + escape(shape, quote=True) + '"/></clipPath></defs>'
        '<rect width="512" height="512" rx="36" fill="#fff4f3"/>'
        '<ellipse cx="252" cy="416" rx="164" ry="19" fill="#ead8d7" opacity=".65"/>'
        f'<path d="{geom["body"]}" fill="{base}" stroke="#987d79" stroke-width="7" stroke-linejoin="round"/>'
        f'{extras}<path d="{geom["head"]}" fill="{base}" stroke="#987d79" stroke-width="7" stroke-linejoin="round"/>'
        f'{geom["ears"]}<g clip-path="url(#fur)">{pattern_shapes}{marks[appearance.marking]}</g>'
        f'<ellipse cx="212" cy="234" rx="14" ry="19" fill="{eyes}"/>'
        f'<ellipse cx="292" cy="234" rx="14" ry="19" fill="{eyes}"/>'
        '<circle cx="216" cy="227" r="5" fill="white"/><circle cx="296" cy="227" r="5" fill="white"/>'
        '<ellipse cx="177" cy="267" rx="21" ry="12" fill="#efb4b6" opacity=".75"/>'
        '<ellipse cx="329" cy="267" rx="21" ry="12" fill="#efb4b6" opacity=".75"/>'
        '<path d="M244 263 Q252 273 260 263" fill="none" stroke="#805d5c" stroke-width="4" stroke-linecap="round"/>'
        '</svg>'
    )
