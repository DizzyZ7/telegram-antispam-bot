"""Species-aware, bounded SVG coat patterns.

All elements are composited inside the existing fur clipPath. Coordinates are
fixed for the first three v1 master silhouettes. No external SVG is accepted.
"""
from __future__ import annotations

from hashlib import sha256
from .core import Appearance

def coat_pattern(appearance: Appearance) -> str:
    kind = appearance.pattern
    species = appearance.species
    if kind == "solid":
        return ""
    digest = sha256(appearance.canonical_dna().encode("ascii")).digest()
    if species == "kitten":
        if kind == "tabby":
            return '<g fill="none" stroke="#967b70" stroke-width="10" stroke-linecap="round" opacity=".7">' + "".join(
                f'<path d="M{176+i*28} 190 q-14 22 2 38"/>' for i in range(6)
            ) + '</g>'
        if kind in ("calico", "tortoiseshell", "bicolor"):
            colors = ("#d9975d", "#695c60", "#fff9ef") if kind == "calico" else (
                ("#a87955", "#54484d") if kind == "tortoiseshell" else ("#fff9ef",)
            )
            return '<g opacity=".9">' + "".join(
                f'<ellipse cx="{158+digest[i]%196}" cy="{205+digest[i+8]%160}" rx="{19+digest[i+16]%25}" ry="{16+digest[i+20]%19}" fill="{colors[i%len(colors)]}"/>'
                for i in range(5)
            ) + '</g>'
    if species == "foal":
        if kind == "pinto":
            return '<g fill="#fff8e9">' + "".join(
                f'<ellipse cx="{150+digest[i]%190}" cy="{245+digest[i+8]%105}" rx="{24+digest[i+16]%25}" ry="{20+digest[i+20]%28}"/>'
                for i in range(4)
            ) + '</g>'
        if kind == "roan":
            return '<g fill="#fff8e9" opacity=".52">' + "".join(
                f'<circle cx="{135+digest[i%32]%235}" cy="{233+digest[(i+11)%32]%130}" r="2.5"/>'
                for i in range(80)
            ) + '</g>'
        if kind == "dapple":
            return '<g fill="none" stroke="#fff8e9" opacity=".58" stroke-width="6">' + "".join(
                f'<circle cx="{150+digest[i]%195}" cy="{239+digest[i+8]%123}" r="{9+digest[i+16]%9}"/>'
                for i in range(7)
            ) + '</g>'
    if species == "calf":
        if kind in ("piebald", "spotted"):
            color = "#fff8ee" if kind == "piebald" else "#78564f"
            return '<g fill="' + color + '">' + "".join(
                f'<ellipse cx="{151+digest[i]%196}" cy="{223+digest[i+8]%142}" rx="{17+digest[i+16]%32}" ry="{14+digest[i+20]%26}"/>'
                for i in range(5)
            ) + '</g>'
    raise ValueError(f"Unsupported pattern {kind!r} for {species!r}")
