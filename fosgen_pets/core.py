"""Pure, deterministic Fosgen Pets domain logic.

No Telegram handlers or database writes here. Rendering assets are versioned
separately: DNA is a stable recipe, not a promise that arbitrary PNGs are unique.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import unicodedata
from typing import Mapping

UNLOCK_MESSAGES = 100
SPECIES = (
    "kitten", "puppy", "foal", "calf", "bunny", "fox", "badger",
    "fawn", "raccoon", "capybara", "alpaca", "otter",
)
# Compatible palette and markings must be refined per species by artists.
PALETTES: dict[str, tuple[str, ...]] = {
    "kitten": ("cream", "ginger", "silver", "black", "chocolate", "blue"),
    "puppy": ("cream", "golden", "chocolate", "black", "silver"),
    "foal": ("chestnut", "bay", "black", "palomino", "grey"),
    "calf": ("cream", "brown", "black", "golden"),
    "bunny": ("white", "cream", "brown", "grey"),
    "fox": ("red", "silver", "arctic", "cream"),
    "badger": ("grey", "silver", "brown"),
    "fawn": ("tan", "chestnut", "cream"),
    "raccoon": ("grey", "silver", "brown"),
    "capybara": ("brown", "golden", "tan"),
    "alpaca": ("white", "cream", "brown", "black"),
    "otter": ("brown", "chocolate", "silver"),
}
PATTERNS: dict[str, tuple[str, ...]] = {
    "kitten": ("solid", "tabby", "bicolor", "calico", "tortoiseshell"),
    "puppy": ("solid", "bicolor", "spotted", "sable"),
    "foal": ("solid", "pinto", "roan", "dapple"),
    "calf": ("solid", "piebald", "spotted"),
    "bunny": ("solid", "broken", "pointed"),
    "fox": ("solid", "socks", "mask"),
    "badger": ("stripe", "mask"),
    "fawn": ("solid", "spotted"),
    "raccoon": ("mask", "stripe"),
    "capybara": ("solid", "shaded"),
    "alpaca": ("solid", "piebald"),
    "otter": ("solid", "bib"),
}
EYE_COLORS = ("brown", "amber", "hazel", "green", "blue")
MARKINGS = ("none", "star", "heart", "blaze", "freckle", "sock_left", "sock_right")
TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
CURRENT_ASSET_VERSION = 2
MAX_PATTERN_SEED = 999999
# Only geometrically distinct variants are valid in v2. V1 keeps its old range.
MARKING_VARIANT_COUNTS = {
    "none": 1, "star": 35, "heart": 35, "blaze": 35, "freckle": 4,
    "sock_left": 9, "sock_right": 9,
}


@dataclass(frozen=True)
class MessageEligibility:
    user_id: int
    chat_id: int
    message_id: int
    text: str
    is_bot: bool = False
    moderated: bool = False
    opted_in: bool = False


def eligible_message(message: MessageEligibility) -> bool:
    """Conservative local filter; rate limits and duplicates require DB checks."""
    if not isinstance(message.text, str) or not 8 <= len(message.text) <= 4096:
        return False
    text = unicodedata.normalize("NFKC", message.text).strip()
    words = TOKEN_RE.findall(text)
    return (
        message.opted_in and not message.is_bot and not message.moderated
        and message.user_id > 0 and message.chat_id != 0 and message.message_id > 0
        and not text.startswith("/") and len(words) >= 2 and len(text) >= 8
    )


def choose_species(traits: Mapping[str, float]) -> str:
    """Stable gameplay mapping, not a psychological assessment."""
    scores = {
        "kitten": traits.get("curiosity", 0) + traits.get("playfulness", 0),
        "puppy": traits.get("sociability", 0) + traits.get("playfulness", 0),
        "foal": traits.get("energy", 0) + traits.get("curiosity", 0),
        "calf": traits.get("calm", 0) + traits.get("kindness", 0),
        "bunny": traits.get("calm", 0) + traits.get("curiosity", 0),
        "fox": traits.get("curiosity", 0) + traits.get("creativity", 0),
        "badger": traits.get("calm", 0) + traits.get("night", 0),
        "fawn": traits.get("kindness", 0) + traits.get("calm", 0),
        "raccoon": traits.get("playfulness", 0) + traits.get("night", 0),
        "capybara": traits.get("calm", 0) + traits.get("sociability", 0),
        "alpaca": traits.get("kindness", 0) + traits.get("creativity", 0),
        "otter": traits.get("energy", 0) + traits.get("sociability", 0),
    }
    return max(SPECIES, key=lambda species: (scores[species], -SPECIES.index(species)))


@dataclass(frozen=True)
class Appearance:
    species: str
    palette: str
    pattern: str
    eyes: str
    marking: str
    marking_variant: int
    asset_version: int = 1
    pattern_seed: int | None = None

    def canonical_dna(self) -> str:
        fields = dict(self.__dict__)
        # Historical DNA is a public persistence format: do not add even null keys.
        if self.asset_version == 1 and self.pattern_seed is None:
            fields.pop("pattern_seed")
        return json.dumps(fields, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return sha256(self.canonical_dna().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PetProfile:
    owner_user_id: int
    species: str
    appearance: Appearance
    name: str | None = None
    origin_story: str | None = None


def validate_appearance(appearance: Appearance) -> None:
    """Validate finite catalogs before any renderer, persistence or HTML boundary."""
    if not isinstance(appearance.species, str) or appearance.species not in SPECIES:
        raise ValueError("Unknown pet species")
    if appearance.palette not in PALETTES[appearance.species]:
        raise ValueError("Invalid palette")
    if appearance.pattern not in PATTERNS[appearance.species]:
        raise ValueError("Invalid pattern")
    if appearance.eyes not in EYE_COLORS or appearance.marking not in MARKINGS:
        raise ValueError("Invalid feature")
    if type(appearance.marking_variant) is not int or not 0 <= appearance.marking_variant < 4096:
        raise ValueError("Invalid marking variant")
    if type(appearance.asset_version) is not int or appearance.asset_version not in (1, 2):
        raise ValueError("Unsupported asset version")
    if appearance.asset_version == 1:
        if appearance.pattern_seed is not None:
            raise ValueError("Legacy appearances have no pattern seed")
    elif type(appearance.pattern_seed) is not int or not 0 <= appearance.pattern_seed <= MAX_PATTERN_SEED:
        raise ValueError("Pattern seed must be an integer in 0..999999")
    elif appearance.marking_variant >= MARKING_VARIANT_COUNTS[appearance.marking]:
        raise ValueError("Noncanonical marking variant")


def build_appearance(
    species: str, seed: str, attempt: int = 0, *,
    pattern_seed: int | None = None, asset_version: int = CURRENT_ASSET_VERSION,
) -> Appearance:
    """Deterministic candidate. Caller MUST reserve DNA under a DB UNIQUE index."""
    if species not in SPECIES:
        raise ValueError("Unknown pet species")
    if not isinstance(seed, str) or not 1 <= len(seed) <= 1024 or type(attempt) is not int or attempt < 0:
        raise ValueError("Seed must be nonempty and attempt nonnegative")
    if type(asset_version) is not int or asset_version not in (1, 2):
        raise ValueError("Unsupported asset version")
    if pattern_seed is not None and (type(pattern_seed) is not int or not 0 <= pattern_seed <= MAX_PATTERN_SEED):
        raise ValueError("Pattern seed must be an integer in 0..999999")
    if asset_version == 1 and pattern_seed is not None:
        raise ValueError("Legacy appearances have no pattern seed")
    digest = sha256(f"fosgen-pets-v{asset_version}:{species}:{seed}:{attempt}".encode()).digest()
    def pick(options: tuple[str, ...], offset: int) -> str:
        return options[int.from_bytes(digest[offset:offset + 4], "big") % len(options)]
    marking = pick(MARKINGS, 12)
    if asset_version == 2:
        roll = int.from_bytes(digest[12:16], "big") % 10000
        marking = next(name for ceiling, name in (
            (6500, "none"), (7500, "freckle"), (8300, "sock_left"),
            (9100, "sock_right"), (9700, "blaze"), (9950, "star"), (10000, "heart"),
        ) if roll < ceiling)
    result = Appearance(
        species=species,
        palette=pick(PALETTES[species], 0),
        pattern=pick(PATTERNS[species], 4),
        eyes=pick(EYE_COLORS, 8),
        marking=marking,
        marking_variant=int.from_bytes(digest[16:20], "big") % (MARKING_VARIANT_COUNTS[marking] if asset_version == 2 else 4096),
        asset_version=asset_version,
        pattern_seed=(pattern_seed if pattern_seed is not None else int.from_bytes(digest[20:24], "big") % 1000000) if asset_version == 2 else None,
    )
    validate_appearance(result)
    return result
