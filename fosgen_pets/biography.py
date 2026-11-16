"""Frozen logical traits, independent of owner identity and rendering.

Sex v1 applies to every DNA version without modifying the canonical DNA or art.
Never change its namespace, bit selection or mapping: add a new explicitly
stored trait version if a future genetics system requires a different rule.
"""
from hashlib import sha256

from .core import Appearance

SEX_VERSION = 1


def pet_sex(appearance: Appearance) -> str:
    value = sha256(b"fosgen-pets-sex-v1:" + appearance.fingerprint().encode("ascii")).digest()[0]
    return "female" if value & 1 else "male"


def sex_label(appearance: Appearance) -> str:
    return "девочка ♀" if pet_sex(appearance) == "female" else "мальчик ♂"
