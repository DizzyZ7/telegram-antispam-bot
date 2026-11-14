"""Fosgen Pets: opt-in Telegram-native companion system (foundation)."""

from .core import Appearance, PetProfile, MessageEligibility, build_appearance, choose_species, eligible_message

__all__ = ["Appearance", "PetProfile", "MessageEligibility", "build_appearance", "choose_species", "eligible_message"]
