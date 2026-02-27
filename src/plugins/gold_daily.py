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


def _extract_rates(payload: dict[str, Any]) -> dict[str, float]:
    candidates: list[Any] = [
        payload.get("rates"),
        (payload.get("data") or {}).get("rates") if isinstance(payload.get("data"), dict) else None,
        (payload.get("result") or {}).get("rates") if isinstance(payload.get("result"), dict) else None,
    ]
    for raw_rates in candidates:
        if not isinstance(raw_rates, dict):
            continue
        rates: dict[str, float] = {}
        for key, value in raw_rates.items():
            num = _to_float(value)
            if num is not None:
                rates[str(key).upper()] = num
        if rates:
            return rates
    return {}


def _symbol_to_currency(symbol: str) -> str | None:
    normalized = symbol.strip().upper()
    mapping = {
        "XAUUSD": "USD",
        "XAUCNY": "CNY",
        "XAUUSD_CNY": "CNY",
    }
    return mapping.get(normalized)


def _fetch_freegoldprice_rates(provider_cfg: dict[str, Any], currencies: list[str]) -> dict[str, float]:
    api_key_env = str(provider_cfg.get("api_key_env") or "FREEGOLDPRICE_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"缺少 API key 环境变量: {api_key_env}")

    endpoint = str(provider_cfg.get("endpoint") or "https://freegoldprice.org/api/v2").strip()
    action = str(provider_cfg.get("action") or "GSJ").strip()

    params = {
        "key": api_key,
        "action": action,
    }
    resp = requests.get(endpoint, params=params, timeout=10)
    if resp.status_code != 200:
        body_preview = resp.text[:200].replace("\n", " ")
        raise ValueError(f"金价接口请求失败: status={resp.status_code}, body={body_preview!r}")

    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("金价接口返回格式异常")

    error_msg = ""
    if isinstance(data.get("error"), dict):
        error_msg = str(
            data["error"].get("info")
            or data["error"].get("message")
            or data["error"].get("detail")
            or ""
        )
    elif isinstance(data.get("error"), str):
        error_msg = data["error"]

    gold_block = data.get("gold")
    if not isinstance(gold_block, dict):
        if error_msg:
            raise ValueError(f"金价接口未返回 gold 数据（{error_msg}）")
        raise ValueError("金价接口未返回 gold 数据")

    rates: dict[str, float] = {}
    for cur in currencies:
        cur_key = str(cur).upper()
        entry = gold_block.get(cur_key)
        if not isinstance(entry, dict):
            continue
        price = _to_float(entry.get("ask") or entry.get("bid"))
        if price is not None:
            rates[cur_key] = price

    if not rates:
        raise ValueError("金价接口未返回所需币种的报价")
    return rates


def _fetch_metalpriceapi_rates(provider_cfg: dict[str, Any], currencies: list[str]) -> dict[str, float]:
    api_key_env = str(provider_cfg.get("api_key_env") or "METALPRICE_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"缺少 API key 环境变量: {api_key_env}")

    endpoint = str(provider_cfg.get("endpoint") or "https://api.metalpriceapi.com/v1/latest").strip()
    base_currency = str(provider_cfg.get("base_currency") or "XAU").strip().upper()
    if base_currency != "XAU":
        raise ValueError("provider.base_currency 仅支持 XAU")

    params = {
        "api_key": api_key,
        "base": base_currency,
        "currencies": ",".join(currencies),
    }
    resp = requests.get(endpoint, params=params, timeout=10)
    if resp.status_code != 200:
        raise ValueError(f"金价接口请求失败: status={resp.status_code}")

    data = resp.json()
    if isinstance(data, dict) and data.get("success") is False:
        err_msg = ""
        if isinstance(data.get("error"), dict):
            err_msg = str(data["error"].get("info") or data["error"].get("message") or "")
        raise ValueError(err_msg or "金价接口返回失败")

    rates = _extract_rates(data if isinstance(data, dict) else {})
    if not rates:
        raise ValueError("金价接口未返回可用 rates")
    return rates


def _fetch_quotes(
    symbols: list[str],
    symbol_names: dict[str, str],
    provider_cfg: dict[str, Any],
) -> list[_GoldQuote]:
    currencies: list[str] = []
    for symbol in symbols:
        currency = _symbol_to_currency(symbol)
        if currency and currency not in currencies:
            currencies.append(currency)

    if not currencies:
        return [
            _GoldQuote(
                symbol=s,
                name=symbol_names.get(s, s),
                failed=True,
                error_msg="无可用 symbol，请使用 XAUUSD/XAUCNY",
            )
            for s in symbols
        ]

    provider_type = str(provider_cfg.get("type") or "metalpriceapi").strip().lower()
    try:
        if provider_type == "metalpriceapi":
            rates = _fetch_metalpriceapi_rates(provider_cfg, currencies)
        elif provider_type == "freegoldprice":
            rates = _fetch_freegoldprice_rates(provider_cfg, currencies)
        else:
            return [
                _GoldQuote(
                    symbol=s,
                    name=symbol_names.get(s, s),
                    failed=True,
                    error_msg=f"暂不支持 provider.type={provider_type}",
                )
                for s in symbols
            ]
    except Exception as e:
        return [
            _GoldQuote(
                symbol=s,
                name=symbol_names.get(s, s),
                failed=True,
                error_msg=str(e),
            )
            for s in symbols
        ]

    quotes: list[_GoldQuote] = []
    for symbol in symbols:
        display_name = symbol_names.get(symbol) or symbol
        currency = _symbol_to_currency(symbol)
        if currency is None:
            quotes.append(
                _GoldQuote(
                    symbol=symbol,
                    name=display_name,
                    failed=True,
                    error_msg="不支持的 symbol",
                )
            )
            continue
        price = _to_float(rates.get(currency))
        if price is None:
            quotes.append(
                _GoldQuote(
                    symbol=symbol,
                    name=display_name,
                    failed=True,
                    error_msg=f"缺少 {currency} 报价",
                )
            )
            continue
        quotes.append(
            _GoldQuote(
                symbol=symbol,
                name=display_name,
                current=price,
            )
        )
    return quotes


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
        quotes = _fetch_quotes(symbols, symbol_names, provider_cfg)

        blocks: list[str] = []
        blocks.append(
            f"<h2 style=\"margin:0 0 8px;font-size:15px;font-weight:600;\">今日金价简报（{date_str}）</h2>"
        )
        blocks.append(
            "<table style=\"width:100%;border-collapse:collapse;font-size:13px;\">"
            "<thead>"
            "<tr>"
            "<th style=\"text-align:left;padding:4px 6px;\">品种</th>"
            "<th style=\"text-align:right;padding:4px 6px;\">现价</th>"
            "<th style=\"text-align:right;padding:4px 6px;\">涨跌</th>"
            "<th style=\"text-align:right;padding:4px 6px;\">昨/今</th>"
            "</tr>"
            "</thead>"
            "<tbody>"
        )

        failed_quotes: list[_GoldQuote] = []
        for q in quotes:
            if q.failed:
                failed_quotes.append(q)
                continue
            current_str = f"{q.current:.{price_precision}f}"
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
            prev_str = f"{q.prev_close:.{price_precision}f}" if q.prev_close is not None else "--"
            open_str = f"{q.open_today:.{price_precision}f}" if q.open_today is not None else "--"
            blocks.append(
                "<tr>"
                f"<td style=\"padding:4px 6px;border-top:1px solid #eee;\">{q.name}</td>"
                f"<td style=\"padding:4px 6px;border-top:1px solid #eee;text-align:right;\">{current_str}</td>"
                f"<td style=\"padding:4px 6px;border-top:1px solid #eee;text-align:right;\">{change_pct_str} / {change_abs_str}</td>"
                f"<td style=\"padding:4px 6px;border-top:1px solid #eee;text-align:right;\">{prev_str} / {open_str}</td>"
                "</tr>"
            )

        blocks.append("</tbody></table>")
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
