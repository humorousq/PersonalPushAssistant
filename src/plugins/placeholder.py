"""Placeholder plugin for testing runner and channel (stage 3)."""
from __future__ import annotations

from src.models import PluginContext, PushMessage


class PlaceholderPlugin:
    """Returns a single fixed message; used to verify runner + PushPlus."""

    id = "placeholder"

    def run(self, ctx: PluginContext) -> list[PushMessage]:
        return [
            PushMessage(
                title="Test",
                body="Hello from Personal Push Assistant",
                format="text",
                target_recipient=None,
            )
        ]
