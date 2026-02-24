"""stocks.daily-brief plugin: quote + optional news (spec 4)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

from src.models import PluginContext, PushMessage

logger = logging.getLogger(__name__)

# Sina: 0=name, 1=今开, 2=昨收, 3=现价
SINA_HQ_URL = "http://hq.sinajs.cn/list="
EASTMONEY_NEWS_URL = "https://so.eastmoney.com/news/s"


def _symbol_to_sina(symbol: str) -> str:
    """600519.SH -> sh600519, 000858.SZ -> sz000858."""
    s = symbol.strip().upper()
    if s.endswith(".SH"):
        return "sh" + s[:-3]
    if s.endswith(".SZ"):
        return "sz" + s[:-3]
    if s.startswith("6"):
        return "sh" + s
    return "sz" + s


@dataclass
class _Quote:
    symbol: str
    name: str
    prev_close: float
    open_today: float
    current: float
    change_pct: float
    failed: bool = False
    error_msg: str = ""


def _fetch_quotes(symbols: list[str]) -> list[_Quote]:
    if not symbols:
        return []
    sina_codes = [_symbol_to_sina(s) for s in symbols]
    list_param = ",".join(sina_codes)
    url = SINA_HQ_URL + list_param
    try:
        resp = requests.get(url, timeout=10, headers={"Referer": "https://finance.sina.com.cn/"})
        resp.encoding = resp.apparent_encoding or "gbk"
        text = resp.text
    except requests.RequestException as e:
        logger.warning("Sina quote request failed: %s", e)
        return [_Quote(s, "", 0.0, 0.0, 0.0, 0.0, failed=True, error_msg=str(e)) for s in symbols]

    quotes: list[_Quote] = []
    for i, symbol in enumerate(symbols):
        # Response format: var hq_str_sh600519="name,open,prev_close,current,...";
        pattern = re.compile(r'var\s+hq_str_' + re.escape(sina_codes[i]) + r'="([^"]*)"')
        m = pattern.search(text)
        if not m:
            quotes.append(_Quote(symbol, "", 0.0, 0.0, 0.0, 0.0, failed=True, error_msg="无数据"))
            continue
        parts = m.group(1).split(",")
        if len(parts) < 4:
            quotes.append(_Quote(symbol, parts[0] if parts else "", 0.0, 0.0, 0.0, 0.0, failed=True, error_msg="字段不足"))
            continue
        try:
            name = parts[0].strip()
            open_t = float(parts[1])
            prev = float(parts[2])
            curr = float(parts[3])
            change_pct = ((curr - prev) / prev * 100) if prev else 0.0
            quotes.append(_Quote(symbol, name, prev, open_t, curr, change_pct))
        except (ValueError, IndexError) as e:
            quotes.append(_Quote(symbol, "", 0.0, 0.0, 0.0, 0.0, failed=True, error_msg=str(e)))
    return quotes


def _fetch_news(keyword: str, limit: int) -> list[tuple[str, str]]:
    """Return list of (title, url) for keyword, at most limit items."""
    if limit <= 0:
        return []
    try:
        resp = requests.get(EASTMONEY_NEWS_URL, params={"keyword": keyword}, timeout=10)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[tuple[str, str]] = []
        # Common structure: news list items with title link
        for a in soup.select("div.news_item_t a, .newslist a, a[href*='eastmoney.com']")[: limit * 2]:
            href = a.get("href") or ""
            if "eastmoney.com" not in href and not href.startswith("http"):
                continue
            if not href.startswith("http"):
                href = "https://so.eastmoney.com" + href if href.startswith("/") else "https://" + href
            title = (a.get_text() or "").strip()
            if title and len(title) > 2:
                items.append((title[:80], href))
                if len(items) >= limit:
                    break
        return items[:limit]
    except Exception as e:
        logger.warning("News fetch failed for %s: %s", keyword, e)
        return []


class StocksDailyBriefPlugin:
    """Plugin id: stocks.daily-brief. Outputs one Markdown PushMessage (spec 4)."""

    id = "stocks.daily-brief"

    def run(self, ctx: PluginContext) -> list[PushMessage]:
        cfg: dict[str, Any] = ctx.plugin_config or {}
        symbols = cfg.get("symbols")
        if not symbols or not isinstance(symbols, (list, tuple)):
            raise ValueError("plugin_config must have 'symbols' (list)")
        symbols = [str(s).strip() for s in symbols if s]
        with_news = cfg.get("with_news", False)
        if not isinstance(with_news, bool):
            with_news = bool(with_news)
        news_per_symbol = cfg.get("news_per_symbol", 3)
        if not isinstance(news_per_symbol, (int, float)):
            try:
                news_per_symbol = int(news_per_symbol)
            except (TypeError, ValueError):
                news_per_symbol = 3
        news_per_symbol = max(0, int(news_per_symbol))

        date_str = ctx.now.strftime("%Y-%m-%d")
        lines: list[str] = ["# 今日股票简报（" + date_str + "）", "", "## 股票概览", ""]

        quotes = _fetch_quotes(symbols)
        for q in quotes:
            if q.failed:
                lines.append(f"- {q.symbol}：获取失败（{q.error_msg}）")
            else:
                sign = "+" if q.change_pct >= 0 else ""
                lines.append(
                    f"- {q.symbol} {q.name}：现价 {q.current:.2f}（{sign}{q.change_pct:.2f}%），昨收 {q.prev_close:.2f}，今开 {q.open_today:.2f}"
                )

        if with_news and news_per_symbol > 0:
            lines.append("")
            lines.append("## 新闻")
            for q in quotes:
                if q.failed or not q.name:
                    continue
                lines.append("")
                lines.append(f"### {q.symbol} {q.name}")
                news_list = _fetch_news(q.name, news_per_symbol)
                if not news_list:
                    lines.append("暂无相关新闻。")
                else:
                    for idx, (title, url) in enumerate(news_list, 1):
                        lines.append(f"{idx}. {title} - [链接]({url})")

        body = "\n".join(lines).strip()
        return [
            PushMessage(
                title=f"股票简报 {date_str}",
                body=body,
                format="markdown",
                target_recipient=None,
            )
        ]
