# Plugin registry; Stage 4 will register stocks.daily-brief
from __future__ import annotations

from src.plugins.placeholder import PlaceholderPlugin

PLUGINS: dict[str, type] = {
    "placeholder": PlaceholderPlugin,
}


def get_plugin(plugin_id: str):
    """Return plugin class for plugin_id; raises KeyError if unknown."""
    if plugin_id not in PLUGINS:
        raise KeyError(f"Unknown plugin_id: {plugin_id}")
    return PLUGINS[plugin_id]
