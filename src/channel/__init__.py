"""Channel layer: base + PushPlus; factory by type."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.channel.base import Channel
from src.channel.pushplus import PushPlusChannel

if TYPE_CHECKING:
    pass

_CHANNELS: dict[str, type[Channel]] = {
    "pushplus": PushPlusChannel,
}


def get_channel(channel_type: str) -> type[Channel]:
    """Return channel class for given type (v1 only 'pushplus')."""
    if channel_type not in _CHANNELS:
        raise ValueError(f"Unknown channel type: {channel_type}")
    return _CHANNELS[channel_type]


__all__ = ["Channel", "PushPlusChannel", "get_channel"]
