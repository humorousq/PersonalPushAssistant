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

# Sina A 股: 0=名称, 1=今开, 2=昨收, 3=现价
# Sina 港股: 0=英文名, 1=中文名, 2=今开, 3=昨收, 6=现价
SINA_HQ_URL = "http://hq.sinajs.cn/list="
EASTMONEY_NEWS_URL = "https://so.eastmoney.com/news/s"


def _symbol_to_sina(symbol: str) -> str:
    """Map user symbol to Sina code.

    Examples:
    - 600519.SH -> sh600519
    - 000858.SZ -> sz000858
    - 1024.HK   -> hk01024
    """
    s = symbol.strip().upper()
    if not s:
        return ""
    if s.endswith(".SH"):
        return "sh" + s[:-3]
    if s.endswith(".SZ"):
        return "sz" + s[:-3]
    if s.endswith(".HK"):
        base = s[:-3]
        digits = "".join(ch for ch in base if ch.isdigit())
        if not digits:
            return ""
        # Sina uses 5-digit Hong Kong codes with leading zeros, e.g. hk01024
        return "hk" + digits.zfill(5)
    if s.startswith("6"):
        return "sh" + s
    # Default: treat as SZ A-share style
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
        s_upper = symbol.strip().upper()
        is_hk = s_upper.endswith(".HK")
        # Response format:
        # - A 股: var hq_str_sh600519="name,open,prev_close,current,...";
        # - 港股: var hq_str_hk01024="en_name,cn_name,open,prev_close,...,current,...";
        pattern = re.compile(r'var\s+hq_str_' + re.escape(sina_codes[i]) + r'="([^"]*)"')
        m = pattern.search(text)
        if not m:
            quotes.append(_Quote(symbol, "", 0.0, 0.0, 0.0, 0.0, failed=True, error_msg="无数据"))
            continue
        parts = m.group(1).split(",")
        min_len = 7 if is_hk else 4
        if len(parts) < min_len:
            quotes.append(
                _Quote(
                    symbol,
                    parts[1 if is_hk and len(parts) > 1 else 0] if parts else "",
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    failed=True,
                    error_msg="字段不足",
                )
            )
            continue
        try:
            if is_hk:
                # 港股：0=英文名,1=中文名,2=今开,3=昨收,6=现价
                cn_name = parts[1].strip()
                en_name = parts[0].strip()
                # 若中文名包含至少一个汉字则优先用中文名，否则一律使用英文名，避免乱码
                if re.search(r"[\u4e00-\u9fff]", cn_name):
                    name = cn_name
                else:
                    name = en_name
                open_t = float(parts[2])
                prev = float(parts[3])
                curr = float(parts[6])
            else:
                # A 股：0=名称,1=今开,2=昨收,3=现价
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
    """Return list of (title, url) for keyword, at most limit items.

    当前实现仅作为可选增强，默认关闭（with_news=false）。
    尝试过滤掉明显的广告和推广链接，但仍不保证强相关性。
    """
    if limit <= 0:
        return []
    try:
        resp = requests.get(EASTMONEY_NEWS_URL, params={"keyword": keyword}, timeout=10)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[tuple[str, str]] = []
        # Common structure: news list items with title link
        for a in soup.select("div.news_item_t a, .newslist a, a[href*='eastmoney.com']")[: limit * 5]:
            href = a.get("href") or ""
            if "eastmoney.com" not in href and not href.startswith("http"):
                continue
            if not href.startswith("http"):
                href = "https://so.eastmoney.com" + href if href.startswith("/") else "https://" + href
            title = (a.get_text() or "").strip()
            if not title or len(title) <= 4:
                continue
            # 过滤明显广告 / 推广链接
            lower_title = title.lower()
            if "东方财富" in title or "level-2" in lower_title or "免费版" in title:
                continue
            if "acttg.eastmoney.com" in href:
                continue
            # 要求包含至少一个中文字符，看起来更像新闻
            if not re.search(r"[\u4e00-\u9fff]", title):
                continue
            items.append((title[:80], href))
            if len(items) >= limit:
                break
        return items[:limit]
    except Exception as e:
        logger.warning("News fetch failed for %s: %s", keyword, e)
        return []


class StocksDailyBriefPlugin:
    """Plugin id: stocks.daily-brief. Outputs one HTML PushMessage (spec 4)."""

    id = "stocks.daily-brief"

    def run(self, ctx: PluginContext) -> list[PushMessage]:
        cfg: dict[str, Any] = ctx.plugin_config or {}
        symbols = cfg.get("symbols")
        if not symbols or not isinstance(symbols, (list, tuple)):
            raise ValueError("plugin_config must have 'symbols' (list)")
        symbols = [str(s).strip() for s in symbols if s]
        raw_symbol_names = cfg.get("symbol_names") or {}
        symbol_names: dict[str, str] = (
            dict(raw_symbol_names) if isinstance(raw_symbol_names, dict) else {}
        )
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
        quotes = _fetch_quotes(symbols)

        # HTML 容器
        blocks: list[str] = []
        blocks.append(
            f"<h2 style=\"margin:0 0 8px;font-size:15px;font-weight:600;\">"
            f"今日股票简报（{date_str}）"
            f"</h2>"
        )

        # 主体表格（适配手机，信息紧凑）
        blocks.append(
            "<table style=\"width:100%;border-collapse:collapse;font-size:13px;\">"
            "<thead>"
            "<tr>"
            "<th style=\"text-align:left;padding:4px 6px;\">名称</th>"
            "<th style=\"text-align:right;padding:4px 6px;\">现价</th>"
            "<th style=\"text-align:right;padding:4px 6px;\">涨跌</th>"
            "<th style=\"text-align:right;padding:4px 6px;\">昨/今</th>"
            "</tr>"
            "</thead>"
            "<tbody>"
        )

        failed_quotes: list[_Quote] = []

        for q in quotes:
            if q.failed:
                failed_quotes.append(q)
                continue

            code = q.symbol
            # 名称：优先 symbol_names，其次接口返回名称（仅 A 股；港股默认不信任接口名）
            custom_name = symbol_names.get(code)
            if custom_name:
                name = custom_name
            else:
                if not code.strip().upper().endswith(".HK"):
                    name = q.name
                else:
                    name = ""

            sign = "+" if q.change_pct >= 0 else ""

            # 涨跌幅带颜色（涨红跌绿）
            change_raw = f"{sign}{q.change_pct:.2f}%"
            if q.change_pct > 0:
                change_str = f'<span style="color:#e53935;">{change_raw}</span>'
            elif q.change_pct < 0:
                change_str = f'<span style="color:#1b5e20;">{change_raw}</span>'
            else:
                change_str = change_raw

            # 涨跌额（现价 - 昨收）
            change_abs = q.current - q.prev_close
            change_abs_sign = "+" if change_abs >= 0 else ""
            change_abs_str = f"{change_abs_sign}{change_abs:.2f}"

            blocks.append(
                "<tr>"
                f"<td style=\"padding:4px 6px;border-top:1px solid #eee;\">{name or code}</td>"
                f"<td style=\"padding:4px 6px;border-top:1px solid #eee;text-align:right;\">{q.current:.2f}</td>"
                f"<td style=\"padding:4px 6px;border-top:1px solid #eee;text-align:right;\">{change_str} / {change_abs_str}</td>"
                f"<td style=\"padding:4px 6px;border-top:1px solid #eee;text-align:right;\">{q.prev_close:.2f} / {q.open_today:.2f}</td>"
                "</tr>"
            )

        blocks.append("</tbody></table>")

        # 获取失败的股票单独列出
        if failed_quotes:
            blocks.append(
                "<div style=\"margin-top:8px;color:#e53935;\">获取失败：</div>"
            )
            for q in failed_quotes:
                blocks.append(
                    f"<div style=\"margin-bottom:4px;color:#e53935;\">"
                    f"{q.symbol}：获取失败（{q.error_msg}）"
                    f"</div>"
                )

        # 可选新闻（默认关闭）
        if with_news and news_per_symbol > 0:
            blocks.append("<h3 style=\"margin:8px 0 4px;\">新闻</h3>")
            for q in quotes:
                if q.failed or not q.name:
                    continue
                news_name = symbol_names.get(q.symbol) or q.name
                if q.symbol.strip().upper().endswith(".HK") and q.symbol not in symbol_names:
                    title_html = q.symbol
                else:
                    title_html = f"{q.symbol} {news_name}"
                blocks.append(
                    f"<div style=\"margin-top:6px;font-weight:600;\">{title_html}</div>"
                )
                news_list = _fetch_news(q.name, news_per_symbol)
                if not news_list:
                    blocks.append("<div style=\"color:#757575;\">暂无相关新闻。</div>")
                else:
                    blocks.append("<ul style=\"padding-left:18px;margin:4px 0 8px;\">")
                    for title, url in news_list:
                        blocks.append(
                            f"<li><a href=\"{url}\" target=\"_blank\">{title}</a></li>"
                        )
                    blocks.append("</ul>")

        body = (
            "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "font-size:14px;line-height:1.6;\">"
            + "".join(blocks)
            + "</div>"
        )
        return [
            PushMessage(
                title=f"股票简报 {date_str}",
                body=body,
                format="html",
                target_recipient=None,
            )
        ]
