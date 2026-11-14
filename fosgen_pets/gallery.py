"""Generate reproducible local visual QA galleries without touching Telegram.

Usage:
  python -m fosgen_pets.gallery --output /tmp/fosgen-pets-gallery
Produces index.html and SVGs for all supported species/patterns, plus optional
PNG files if CairoSVG is installed.
"""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

from .core import Appearance, PALETTES, PATTERNS
from .render_svg import SUPPORTED, render_svg
from .portrait import render_png


def build_gallery(output: Path, *, png: bool = False) -> int:
    output.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    count = 0
    for species in sorted(SUPPORTED):
        for pattern in PATTERNS[species]:
            for sample, seed in enumerate((0, 500000, 999999)):
                # Hold every field except Pattern Seed fixed for honest comparison.
                palette = PALETTES[species][0]
                appearance = Appearance(
                    species=species,
                    palette=palette,
                    pattern=pattern,
                    eyes="amber", marking="none", marking_variant=0,
                    asset_version=2, pattern_seed=seed,
                )
                name = f"{species}_{pattern}_{sample}.svg"
                svg = render_svg(appearance)
                (output / name).write_text(svg, encoding="utf-8")
                if png:
                    (output / name.replace(".svg", ".png")).write_bytes(render_png(appearance))
                cards.append(
                    f'<article><img src="{escape(name)}" alt="{escape(species)}" />'
                    f'<h3>{escape(species)} · {escape(pattern)}</h3>'
                    f'<p>{escape(palette)} · Pattern Seed {seed:06d}</p></article>'
                )
                count += 1
    html = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Fosgen Pets QA</title>'
        '<style>body{font-family:system-ui;background:#fff6f5;padding:24px}'
        'main{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px}'
        'article{background:white;border-radius:18px;padding:12px;box-shadow:0 2px 10px #e8d9d9}'
        'img{width:100%;aspect-ratio:1}h3{font-size:16px}p{font-size:13px}</style>'
        f'<h1>Fosgen Pets · Visual QA ({count})</h1><main>{"".join(cards)}</main></html>'
    )
    (output / "index.html").write_text(html, encoding="utf-8")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--png", action="store_true")
    args = parser.parse_args()
    count = build_gallery(args.output, png=args.png)
    print(f"Created {count} sample portraits in {args.output}")


if __name__ == "__main__":
    main()
