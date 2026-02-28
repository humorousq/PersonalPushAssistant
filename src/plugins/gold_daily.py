"""gold.daily-brief plugin: daily gold price overview via configurable provider."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from src.models import PluginContext, PushMessage

logger = logging.getLogger(__name__)


@dataclass
class _GoldQuote:
    symbol: str
    name: str
    current: float = 0.0
    prev_close: float | None = None
    open_today: float | None = None
    change_pct: float | None = None
    change_abs: float | None = None
    failed: bool = False
    error_msg: str = ""


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_optional(value: Any, precision: int = 2) -> str:
    """格式化为带精度的字符串，None 或无法解析时返回 '—'。"""
    num = _to_float(value)
    if num is None:
        return "—"
    return f"{num:.{precision}f}"


def _fetch_tanshuapi_bankgold2(provider_cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """请求探数数据「银行账户黄金(纸黄金)」接口，返回按品种代码索引的行情字典。"""
    api_key_env = str(provider_cfg.get("api_key_env") or "TANSHUAPI_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"缺少 API key 环境变量: {api_key_env}")

    endpoint = str(
        provider_cfg.get("endpoint") or "https://api.tanshuapi.com/api/gold/v1/bankgold2"
    ).strip()
    params = {"key": api_key}
    resp = requests.get(endpoint, params=params, timeout=10)
    if resp.status_code != 200:
        body_preview = resp.text[:200].replace("\n", " ")
        raise ValueError(f"金价接口请求失败: status={resp.status_code}, body={body_preview!r}")

    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("金价接口返回格式异常")
    if data.get("code") != 1:
        msg = data.get("msg") or data.get("message") or "未知错误"
        raise ValueError(f"金价接口返回错误: {msg}")

    inner = data.get("data")
    if not isinstance(inner, dict):
        raise ValueError("金价接口未返回 data")
    raw_list = inner.get("list")
    if not isinstance(raw_list, dict):
        raise ValueError("金价接口未返回 data.list")
    return {str(k).strip().upper(): v for k, v in raw_list.items() if isinstance(v, dict)}


def _parse_changepercent(s: Any) -> float | None:
    """解析 changepercent 字符串如 '0.09%' / '-0.36%' 为 float。"""
    if s is None:
        return None
    t = str(s).strip().replace("%", "").strip()
    if not t:
        return None
    return _to_float(t)


def _fetch_quotes(
    symbols: list[str],
    symbol_names: dict[str, str],
    provider_cfg: dict[str, Any],
    now_dt,
) -> tuple[list[_GoldQuote], dict[str, dict[str, Any]] | None]:
    """仅支持 tanshuapi_bankgold2，返回 (quotes, raw_bankgold2_map)。"""
    provider_type = str(provider_cfg.get("type") or "tanshuapi_bankgold2").strip().lower()
    if provider_type != "tanshuapi_bankgold2":
        return (
            [
                _GoldQuote(
                    symbol=s,
                    name=symbol_names.get(s, s),
                    failed=True,
                    error_msg="仅支持 provider.type=tanshuapi_bankgold2",
                )
                for s in symbols
            ],
            None,
        )

    try:
        raw_list = _fetch_tanshuapi_bankgold2(provider_cfg)
    except Exception as e:
        return (
            [
                _GoldQuote(
                    symbol=s,
                    name=symbol_names.get(s, s),
                    failed=True,
                    error_msg=str(e),
                )
                for s in symbols
            ],
            None,
        )

    quotes: list[_GoldQuote] = []
    for symbol in symbols:
        display_name = symbol_names.get(symbol) or symbol
        item = raw_list.get(symbol)
        if not isinstance(item, dict):
            quotes.append(
                _GoldQuote(
                    symbol=symbol,
                    name=display_name,
                    failed=True,
                    error_msg="接口未返回该品种",
                )
            )
            continue
        price = _to_float(item.get("price"))
        if price is None:
            quotes.append(
                _GoldQuote(
                    symbol=symbol,
                    name=display_name,
                    failed=True,
                    error_msg="缺少价格",
                )
            )
            continue
        prev_close = _to_float(item.get("lastclosingprice"))
        open_today = _to_float(item.get("openingprice"))
        change_abs = _to_float(item.get("changequantity"))
        change_pct = _parse_changepercent(item.get("changepercent"))
        quotes.append(
            _GoldQuote(
                symbol=symbol,
                name=display_name,
                current=price,
                prev_close=prev_close,
                open_today=open_today,
                change_abs=change_abs,
                change_pct=change_pct,
            )
        )
    return (quotes, raw_list)


class GoldDailyBriefPlugin:
    """Plugin id: gold.daily-brief. Outputs one HTML PushMessage."""

    id = "gold.daily-brief"

    def run(self, ctx: PluginContext) -> list[PushMessage]:
        cfg: dict[str, Any] = ctx.plugin_config or {}
        raw_symbols = cfg.get("symbols")
        if not isinstance(raw_symbols, (list, tuple)) or not raw_symbols:
            raise ValueError("plugin_config must have 'symbols' (list)")
        symbols = [str(s).strip().upper() for s in raw_symbols if str(s).strip()]
        if not symbols:
            raise ValueError("plugin_config.symbols is empty")

        raw_symbol_names = cfg.get("symbol_names") or {}
        symbol_names: dict[str, str] = (
            {str(k).strip().upper(): str(v) for k, v in raw_symbol_names.items()}
            if isinstance(raw_symbol_names, dict)
            else {}
        )
        provider_cfg = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}

        raw_display = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
        price_precision = int(raw_display.get("price_precision", 2))
        if price_precision < 0:
            price_precision = 2

        date_str = ctx.now.strftime("%Y-%m-%d")
        quotes, raw_bankgold2 = _fetch_quotes(symbols, symbol_names, provider_cfg, ctx.now)

        blocks: list[str] = []
        blocks.append(
            f"<h2 style=\"margin:0 0 8px;font-size:15px;font-weight:600;\">今日金价简报（{date_str}）</h2>"
        )

        failed_quotes: list[_GoldQuote] = []
        use_bankgold2_ui = raw_bankgold2 is not None

        if use_bankgold2_ui:
            for q in quotes:
                if q.failed:
                    failed_quotes.append(q)
                    continue
                item = raw_bankgold2.get(q.symbol) or {}
                buy_str = _format_optional(item.get("buyprice"), price_precision)
                sell_str = _format_optional(item.get("sellprice"), price_precision)
                prev_str = _format_optional(q.prev_close, price_precision)
                open_str = _format_optional(q.open_today, price_precision)
                unit = (item.get("unit") or "").strip() or "—"
                updatetime = (item.get("updatetime") or "").strip() or "—"
                change_pct_str = "--"
                change_abs_str = "--"
                if q.change_pct is not None:
                    sign = "+" if q.change_pct >= 0 else ""
                    raw_change = f"{sign}{q.change_pct:.2f}%"
                    if q.change_pct > 0:
                        change_pct_str = f'<span style="color:#e53935;">{raw_change}</span>'
                    elif q.change_pct < 0:
                        change_pct_str = f'<span style="color:#1b5e20;">{raw_change}</span>'
                    else:
                        change_pct_str = raw_change
                if q.change_abs is not None:
                    sign_abs = "+" if q.change_abs >= 0 else ""
                    change_abs_str = f"{sign_abs}{q.change_abs:.2f}"
                current_str = f"{q.current:.{price_precision}f}"
                blocks.append(
                    "<div style=\"margin-bottom:12px;padding:10px;border:1px solid #eee;border-radius:6px;background:#fafafa;\">"
                    f"<div style=\"display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;\">"
                    f"<span style=\"font-size:14px;font-weight:600;\">{q.name}</span>"
                    f"<span style=\"font-size:16px;font-weight:600;\">{current_str} <span style=\"font-size:11px;color:#666;font-weight:400;\">{unit}</span></span>"
                    "</div>"
                    f"<div style=\"font-size:12px;color:#666;margin-bottom:4px;\">买入 {buy_str} / 卖出 {sell_str} · 昨收 {prev_str} / 今开 {open_str}</div>"
                    f"<div style=\"font-size:12px;\">涨跌 {change_pct_str}（{change_abs_str}） <span style=\"color:#999;font-size:11px;\">{updatetime}</span></div>"
                    "</div>"
                )
        if failed_quotes:
            blocks.append("<div style=\"margin-top:8px;color:#e53935;\">获取失败：</div>")
            for q in failed_quotes:
                blocks.append(
                    f"<div style=\"margin-bottom:4px;color:#e53935;\">{q.symbol}：获取失败（{q.error_msg}）</div>"
                )

        body = (
            "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "font-size:14px;line-height:1.6;\">"
            + "".join(blocks)
            + "</div>"
        )
        return [
            PushMessage(
                title=f"金价简报 {date_str}",
                body=body,
                format="html",
                target_recipient=None,
            )
        ]
