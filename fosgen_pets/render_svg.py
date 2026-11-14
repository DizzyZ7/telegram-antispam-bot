"""Version dispatch. Never upgrade a stored animal's DNA implicitly.

render_v1/patterns_v1 are frozen historical recipes; new art belongs in a new
version. Permanent portraits are served from stored PNG bytes, not regenerated.
"""
from .core import Appearance, SPECIES, validate_appearance

SUPPORTED = frozenset(SPECIES)


def render_svg(appearance: Appearance) -> str:
    validate_appearance(appearance)
    if appearance.asset_version == 1:
        from .render_v1 import render_svg as render_legacy
        return render_legacy(appearance)
    from .graphics_v2 import render_v2
    return render_v2(appearance)
