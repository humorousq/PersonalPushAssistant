"""Channel abstraction (spec 6)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import PushMessage


class Channel(ABC):
    """Abstract channel: send(msg, channel_config) -> None."""

    @abstractmethod
    def send(self, msg: PushMessage, channel_config: dict) -> None:
        """Send message using the given channel config (e.g. token, topic)."""
        ...
