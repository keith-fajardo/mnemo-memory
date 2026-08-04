"""Explicitly installed on-device embedding adapters; no remote provider lives here."""

from .fastembed import FastEmbedLocalProvider

__all__ = ["FastEmbedLocalProvider"]
