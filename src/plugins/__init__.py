# Plugin registry
from __future__ import annotations

from src.plugins.gold_daily import GoldDailyBriefPlugin
from src.plugins.placeholder import PlaceholderPlugin
from src.plugins.stocks_daily import StocksDailyBriefPlugin

PLUGINS: dict[str, type] = {
    "placeholder": PlaceholderPlugin,
    "gold.daily-brief": GoldDailyBriefPlugin,
    "stocks.daily-brief": StocksDailyBriefPlugin,
}


def get_plugin(plugin_id: str):
    """Return plugin class for plugin_id; raises KeyError if unknown."""
    if plugin_id not in PLUGINS:
        raise KeyError(f"Unknown plugin_id: {plugin_id}")
    return PLUGINS[plugin_id]
