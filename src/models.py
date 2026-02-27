"""Core data models for plugin/channel layer (spec 2.1-2.3)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass
class PushMessage:
    """Standard message structure between plugin and channel layer (spec 2.1)."""

    title: str
    body: str
    format: Literal["text", "markdown", "html"]
    target_recipient: str | None = None
    priority: str | None = None
    tags: list[str] | None = None


@dataclass
class PluginContext:
    """Context passed to plugins at execution time (spec 2.2)."""

    now: datetime
    recipient_id: str
    plugin_config: dict
    global_config: dict


class ContentPlugin(Protocol):
    """Plugin protocol: id + run(ctx) -> list[PushMessage] (spec 2.3)."""

    @property
    def id(self) -> str:
        """Plugin unique id, e.g. 'stocks.daily-brief'."""
        ...

    def run(self, ctx: PluginContext) -> list[PushMessage]:
        """Generate messages from context; target_recipient may be None, set by runner."""
        ...
