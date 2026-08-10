"""Explicitly installed on-device embedding adapters; no remote provider lives here."""

from .fastembed import FastEmbedLocalProvider
from .potion import (
    POTION_MODEL_ID,
    POTION_MODEL_REVISION,
    LocalPotionRouterSettingsStore,
    PotionLocalMemoryRouter,
    PotionModelInstaller,
    PotionRouterError,
    PotionRouterSettings,
    verify_potion_model,
)

__all__ = [
    "POTION_MODEL_ID",
    "POTION_MODEL_REVISION",
    "FastEmbedLocalProvider",
    "LocalPotionRouterSettingsStore",
    "PotionLocalMemoryRouter",
    "PotionModelInstaller",
    "PotionRouterError",
    "PotionRouterSettings",
    "verify_potion_model",
]
