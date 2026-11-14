"""Pure, deterministic Fosgen Pets domain logic.

No Telegram handlers or database writes here. Rendering assets are versioned
separately: DNA is a stable recipe, not a promise that arbitrary PNGs are unique.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
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
TOKEN_RE = re.compile(r"\w+", re.UNICODE)


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
    words = TOKEN_RE.findall(message.text.strip())
    return (
        message.opted_in and not message.is_bot and not message.moderated
        and message.user_id > 0 and message.chat_id != 0 and message.message_id > 0
        and len(words) >= 2 and len(message.text.strip()) >= 8
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

    def canonical_dna(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return sha256(self.canonical_dna().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PetProfile:
    owner_user_id: int
    species: str
    appearance: Appearance
    name: str | None = None
    origin_story: str | None = None


def build_appearance(species: str, seed: str, attempt: int = 0) -> Appearance:
    """Deterministic candidate. Caller MUST reserve DNA under a DB UNIQUE index."""
    if species not in SPECIES:
        raise ValueError("Unknown pet species")
    if not seed or attempt < 0:
        raise ValueError("Seed must be nonempty and attempt nonnegative")
    digest = sha256(f"fosgen-pets-v1:{species}:{seed}:{attempt}".encode()).digest()
    def pick(options: tuple[str, ...], offset: int) -> str:
        return options[int.from_bytes(digest[offset:offset + 4], "big") % len(options)]
    return Appearance(
        species=species,
        palette=pick(PALETTES[species], 0),
        pattern=pick(PATTERNS[species], 4),
        eyes=pick(EYE_COLORS, 8),
        marking=pick(MARKINGS, 12),
        marking_variant=int.from_bytes(digest[16:20], "big") % 4096,
    )
