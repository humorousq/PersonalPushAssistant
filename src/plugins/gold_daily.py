"""gold.daily-brief plugin: daily gold price overview via configurable provider."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import timedelta
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

    payload: dict[str, Any] = {}
    action_block = data.get(action)
    if isinstance(action_block, dict):
        payload = action_block
    else:
        payload = data

    gold_block = payload.get("gold") or payload.get("Gold")
    if not isinstance(gold_block, dict):
        if error_msg:
            raise ValueError(f"金价接口未返回 gold 数据（{error_msg}）")
        data_preview = repr(data)
        if len(data_preview) > 200:
            data_preview = data_preview[:200] + "...（截断）"
        raise ValueError(f"金价接口未返回 gold 数据（响应片段: {data_preview}）")

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


def _fetch_metalpriceapi_rates(
    provider_cfg: dict[str, Any], currencies: list[str]
) -> dict[str, float]:
    api_key_env = str(provider_cfg.get("api_key_env") or "METALPRICE_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"缺少 API key 环境变量: {api_key_env}")

    base_currency = str(provider_cfg.get("base_currency") or "XAU").strip().upper()
    if base_currency != "XAU":
        raise ValueError("provider.base_currency 仅支持 XAU")

    endpoint = str(provider_cfg.get("endpoint") or "https://api.metalpriceapi.com/v1/latest").strip()
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

    # metalpriceapi 默认按每金衡盎司计价；支持通过 provider.unit 配置按克换算
    unit = str(provider_cfg.get("unit") or "ounce").strip().lower()
    if unit == "gram":
        ounce_to_gram = 31.1034768
        rates = {k: v / ounce_to_gram for k, v in rates.items()}
    return rates


def _fetch_metalpriceapi_rates_on_date(
    provider_cfg: dict[str, Any], currencies: list[str], date_str: str
) -> dict[str, float]:
    api_key_env = str(provider_cfg.get("api_key_env") or "METALPRICE_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"缺少 API key 环境变量: {api_key_env}")

    base_currency = str(provider_cfg.get("base_currency") or "XAU").strip().upper()
    if base_currency != "XAU":
        raise ValueError("provider.base_currency 仅支持 XAU")

    endpoint = f"https://api.metalpriceapi.com/v1/{date_str}"
    params = {
        "api_key": api_key,
        "base": base_currency,
        "currencies": ",".join(currencies),
    }
    resp = requests.get(endpoint, params=params, timeout=10)
    if resp.status_code != 200:
        raise ValueError(f"历史金价接口请求失败: status={resp.status_code}")

    data = resp.json()
    if isinstance(data, dict) and data.get("success") is False:
        err_msg = ""
        if isinstance(data.get("error"), dict):
            err_msg = str(data["error"].get("info") or data["error"].get("message") or "")
        raise ValueError(err_msg or "历史金价接口返回失败")

    rates = _extract_rates(data if isinstance(data, dict) else {})
    if not rates:
        raise ValueError("历史金价接口未返回可用 rates")

    unit = str(provider_cfg.get("unit") or "ounce").strip().lower()
    if unit == "gram":
        ounce_to_gram = 31.1034768
        rates = {k: v / ounce_to_gram for k, v in rates.items()}
    return rates


def _fetch_metalpriceapi_fx_rates(
    provider_cfg: dict[str, Any],
    base: str,
    currencies: list[str],
) -> dict[str, float]:
    api_key_env = str(provider_cfg.get("api_key_env") or "METALPRICE_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"缺少 API key 环境变量: {api_key_env}")

    base_currency = str(base or "CNY").strip().upper()

    endpoint = str(provider_cfg.get("endpoint") or "https://api.metalpriceapi.com/v1/latest").strip()
    params = {
        "api_key": api_key,
        "base": base_currency,
        "currencies": ",".join(currencies),
    }
    resp = requests.get(endpoint, params=params, timeout=10)
    if resp.status_code != 200:
        raise ValueError(f"汇率接口请求失败: status={resp.status_code}")

    data = resp.json()
    if isinstance(data, dict) and data.get("success") is False:
        err_msg = ""
        if isinstance(data.get("error"), dict):
            err_msg = str(data["error"].get("info") or data["error"].get("message") or "")
        raise ValueError(err_msg or "汇率接口返回失败")

    rates = _extract_rates(data if isinstance(data, dict) else {})
    if not rates:
        raise ValueError("汇率接口未返回可用 rates")
    return rates


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
    """返回 (quotes, raw_bankgold2_map)。仅当 provider 为 tanshuapi_bankgold2 时 raw_bankgold2_map 非 None。"""
    provider_type = str(provider_cfg.get("type") or "metalpriceapi").strip().lower()

    if provider_type == "tanshuapi_bankgold2":
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

    currencies: list[str] = []
    for symbol in symbols:
        currency = _symbol_to_currency(symbol)
        if currency and currency not in currencies:
            currencies.append(currency)

    if not currencies:
        return (
            [
                _GoldQuote(
                    symbol=s,
                    name=symbol_names.get(s, s),
                    failed=True,
                    error_msg="无可用 symbol，请使用 XAUUSD/XAUCNY",
                )
                for s in symbols
            ],
            None,
        )

    current_rates: dict[str, float] = {}
    prev_rates: dict[str, float] = {}
    rates: dict[str, float] = {}
    try:
        if provider_type == "metalpriceapi":
            current_rates = _fetch_metalpriceapi_rates(provider_cfg, currencies)
            history_days_raw = provider_cfg.get("history_days")
            try:
                history_days = int(history_days_raw) if history_days_raw is not None else 1
            except (TypeError, ValueError):
                history_days = 1
            if history_days < 1:
                history_days = 1
            target_date = (now_dt.date() - timedelta(days=history_days)).strftime("%Y-%m-%d")
            prev_rates = _fetch_metalpriceapi_rates_on_date(provider_cfg, currencies, target_date)
        elif provider_type == "freegoldprice":
            rates = _fetch_freegoldprice_rates(provider_cfg, currencies)
        else:
            return (
                [
                    _GoldQuote(
                        symbol=s,
                        name=symbol_names.get(s, s),
                        failed=True,
                        error_msg=f"暂不支持 provider.type={provider_type}",
                    )
                    for s in symbols
                ],
                None,
            )
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

    quotes = []
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

        if provider_type == "metalpriceapi":
            current = _to_float(current_rates.get(currency))
            prev_close = _to_float(prev_rates.get(currency))
            if current is None or prev_close is None:
                quotes.append(
                    _GoldQuote(
                        symbol=symbol,
                        name=display_name,
                        failed=True,
                        error_msg=f"缺少 {currency} 报价",
                    )
                )
                continue
            change_abs = current - prev_close
            change_pct = (change_abs / prev_close * 100) if prev_close else 0.0
            quotes.append(
                _GoldQuote(
                    symbol=symbol,
                    name=display_name,
                    current=current,
                    prev_close=prev_close,
                    change_abs=change_abs,
                    change_pct=change_pct,
                )
            )
        else:
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
    return (quotes, None)


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

        # 计算昨收使用的历史天数，用于表头展示具体日期
        history_days_raw = provider_cfg.get("history_days")
        try:
            history_days = int(history_days_raw) if history_days_raw is not None else 1
        except (TypeError, ValueError):
            history_days = 1
        if history_days < 1:
            history_days = 1

        fx_cfg = cfg.get("fx") if isinstance(cfg.get("fx"), dict) else {}

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
        else:
            prev_date_label = (ctx.now.date() - timedelta(days=history_days)).strftime("%m-%d")
            history_label = (
                "基准价<br>"
                f"<span style=\"font-size:11px;color:#666;\">{prev_date_label}</span>"
            )
            blocks.append(
                "<table style=\"width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed;\">"
                "<thead>"
                "<tr>"
                "<th style=\"text-align:left;padding:3px 4px;width:40%;\">品种</th>"
                "<th style=\"text-align:right;padding:3px 4px;width:20%;\">现价</th>"
                f"<th style=\"text-align:right;padding:3px 4px;width:20%;\">{history_label}</th>"
                "<th style=\"text-align:right;padding:3px 4px;width:20%;\">涨跌</th>"
                "</tr>"
                "</thead>"
                "<tbody>"
            )
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
                blocks.append(
                    "<tr>"
                    f"<td style=\"padding:3px 4px;border-top:1px solid #eee;\">{q.name}</td>"
                    f"<td style=\"padding:3px 4px;border-top:1px solid #eee;text-align:right;white-space:nowrap;\">{current_str}</td>"
                    f"<td style=\"padding:3px 4px;border-top:1px solid #eee;text-align:right;white-space:nowrap;\">{prev_str}</td>"
                    f"<td style=\"padding:3px 4px;border-top:1px solid #eee;text-align:right;white-space:nowrap;\">{change_pct_str} / {change_abs_str}</td>"
                    "</tr>"
                )
            blocks.append("</tbody></table>")

        # 可选汇率区块：仅 metalpriceapi 支持（基于 base 对多种货币）
        fx_rows: list[tuple[str, str, str]] = []
        fx_base_label = ""
        provider_type = str(provider_cfg.get("type") or "metalpriceapi").strip().lower()
        if fx_cfg and provider_type == "metalpriceapi":
            raw_fx_symbols = fx_cfg.get("currencies")
            if isinstance(raw_fx_symbols, (list, tuple)):
                fx_currencies = [str(c).strip().upper() for c in raw_fx_symbols if str(c).strip()]
            else:
                fx_currencies = []
            if fx_currencies:
                fx_base = str(fx_cfg.get("base") or "CNY").strip().upper()
                fx_labels_raw = fx_cfg.get("labels") or {}
                fx_labels: dict[str, str] = (
                    {str(k).strip().upper(): str(v) for k, v in fx_labels_raw.items()}
                    if isinstance(fx_labels_raw, dict)
                    else {}
                )
                try:
                    fx_rates = _fetch_metalpriceapi_fx_rates(provider_cfg, fx_base, fx_currencies)
                    fx_base_label = fx_base
                    for cur in fx_currencies:
                        rate = _to_float(fx_rates.get(cur))
                        if rate is None:
                            continue
                        name = fx_labels.get(cur, cur)
                        fx_rows.append((name, cur, f"{rate:.4f}"))
                except Exception as e:
                    blocks.append(
                        f"<div style=\"margin-top:8px;color:#e53935;\">汇率获取失败：{e}</div>"
                    )

        if fx_rows and fx_base_label:
            blocks.append(
                "<div style=\"margin-top:10px;font-size:13px;font-weight:600;\">"
                f"汇率参考（1 {fx_base_label}）"
                "</div>"
            )
            blocks.append(
                "<table style=\"width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed;\">"
                "<thead>"
                "<tr>"
                "<th style=\"text-align:left;padding:3px 4px;width:40%;\">货币</th>"
                "<th style=\"text-align:right;padding:3px 4px;width:60%;\">1 基础货币 =</th>"
                "</tr>"
                "</thead>"
                "<tbody>"
            )
            for name, code, rate_str in fx_rows:
                blocks.append(
                    "<tr>"
                    f"<td style=\"padding:3px 4px;border-top:1px solid #eee;\">{name} ({code})</td>"
                    f"<td style=\"padding:3px 4px;border-top:1px solid #eee;text-align:right;white-space:nowrap;\">{rate_str}</td>"
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
