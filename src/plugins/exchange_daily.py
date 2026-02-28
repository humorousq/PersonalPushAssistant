"""exchange.daily-brief plugin: bank exchange rates via tanshuapi bank exchange API."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from src.models import PluginContext, PushMessage

logger = logging.getLogger(__name__)

BANK_NAMES: dict[str, str] = {
    "ICBC": "工商银行",
    "BOC": "中国银行",
    "ABCHINA": "农业银行",
    "BANKCOMM": "交通银行",
    "CCB": "建设银行",
    "CMBCHINA": "招商银行",
    "CEBBANK": "光大银行",
    "SPDB": "浦发银行",
    "CIB": "兴业银行",
    "ECITIC": "中信银行",
}


@dataclass
class _BankResult:
    bank_code: str
    update_time: str
    currency_rates: list[dict[str, Any]]
    failed: bool = False
    error_msg: str = ""


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_optional(value: Any, precision: int = 4) -> str:
    """格式化为带精度的字符串，None 或无法解析时返回 '—'。"""
    num = _to_float(value)
    if num is None:
        return "—"
    return f"{num:.{precision}f}"


def _fetch_bank_exchange(
    bank_code: str,
    provider_cfg: dict[str, Any],
) -> _BankResult:
    """请求探数数据银行汇率 index 接口，返回该银行的汇率数据。"""
    api_key_env = str(provider_cfg.get("api_key_env") or "TANSHUAPI_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        return _BankResult(
            bank_code=bank_code,
            update_time="",
            currency_rates=[],
            failed=True,
            error_msg=f"缺少 API key 环境变量: {api_key_env}",
        )

    endpoint = str(
        provider_cfg.get("endpoint") or "https://api.tanshuapi.com/api/bank_exchange/v1/index"
    ).strip()
    params = {"key": api_key, "bank_code": bank_code}
    try:
        resp = requests.get(endpoint, params=params, timeout=10)
    except Exception as e:
        return _BankResult(
            bank_code=bank_code,
            update_time="",
            currency_rates=[],
            failed=True,
            error_msg=str(e),
        )

    if resp.status_code != 200:
        body_preview = resp.text[:200].replace("\n", " ")
        return _BankResult(
            bank_code=bank_code,
            update_time="",
            currency_rates=[],
            failed=True,
            error_msg=f"请求失败: status={resp.status_code}, body={body_preview!r}",
        )

    try:
        data = resp.json()
    except Exception as e:
        return _BankResult(
            bank_code=bank_code,
            update_time="",
            currency_rates=[],
            failed=True,
            error_msg=f"解析响应失败: {e}",
        )

    if not isinstance(data, dict):
        return _BankResult(
            bank_code=bank_code,
            update_time="",
            currency_rates=[],
            failed=True,
            error_msg="接口返回格式异常",
        )
    if data.get("code") != 1:
        msg = data.get("msg") or data.get("message") or "未知错误"
        return _BankResult(
            bank_code=bank_code,
            update_time="",
            currency_rates=[],
            failed=True,
            error_msg=str(msg),
        )

    inner = data.get("data")
    if not isinstance(inner, dict):
        return _BankResult(
            bank_code=bank_code,
            update_time="",
            currency_rates=[],
            failed=True,
            error_msg="接口未返回 data",
        )

    update_time = str(inner.get("time") or "").strip()
    code_list = inner.get("code_list")
    if not isinstance(code_list, list):
        return _BankResult(
            bank_code=bank_code,
            update_time=update_time,
            currency_rates=[],
            failed=True,
            error_msg="接口未返回 code_list",
        )

    return _BankResult(
        bank_code=bank_code,
        update_time=update_time,
        currency_rates=[c for c in code_list if isinstance(c, dict)],
        failed=False,
    )


class ExchangeDailyBriefPlugin:
    """Plugin id: exchange.daily-brief. Outputs one HTML PushMessage."""

    id = "exchange.daily-brief"

    def run(self, ctx: PluginContext) -> list[PushMessage]:
        cfg: dict[str, Any] = ctx.plugin_config or {}
        raw_banks = cfg.get("banks")
        if not isinstance(raw_banks, (list, tuple)) or not raw_banks:
            raise ValueError("plugin_config must have 'banks' (list)")
        banks = [str(b).strip().upper() for b in raw_banks if str(b).strip()]
        if not banks:
            raise ValueError("plugin_config.banks is empty")

        raw_currencies = cfg.get("currencies")
        if not isinstance(raw_currencies, (list, tuple)) or not raw_currencies:
            raise ValueError("plugin_config must have 'currencies' (list)")
        currencies = [str(c).strip().upper() for c in raw_currencies if str(c).strip()]
        if not currencies:
            raise ValueError("plugin_config.currencies is empty")

        raw_currency_names = cfg.get("currency_names") or {}
        currency_names: dict[str, str] = (
            {str(k).strip().upper(): str(v) for k, v in raw_currency_names.items()}
            if isinstance(raw_currency_names, dict)
            else {}
        )
        provider_cfg = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
        raw_display = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
        price_precision = int(raw_display.get("price_precision", 4))
        if price_precision < 0:
            price_precision = 4

        date_str = ctx.now.strftime("%Y-%m-%d")
        bank_results: list[_BankResult] = []
        for bank_code in banks:
            result = _fetch_bank_exchange(bank_code, provider_cfg)
            bank_results.append(result)

        blocks: list[str] = []
        blocks.append(
            f"<h2 style=\"margin:0 0 8px;font-size:15px;font-weight:600;\">银行汇率简报（{date_str}）</h2>"
        )

        for result in bank_results:
            bank_display = BANK_NAMES.get(result.bank_code, result.bank_code)
            blocks.append(
                f"<h3 style=\"margin:16px 0 8px;font-size:14px;font-weight:600;\">"
                f"{bank_display} ({result.bank_code})</h3>"
            )

            if result.failed:
                blocks.append(
                    f"<div style=\"margin-bottom:12px;color:#e53935;\">"
                    f"获取失败：{result.error_msg}</div>"
                )
                continue

            if result.update_time:
                blocks.append(
                    f"<p style=\"margin:0 0 8px;font-size:12px;color:#666;\">"
                    f"数据更新时间：{result.update_time}</p>"
                )

            code_map = {str(c.get("code", "")).strip().upper(): c for c in result.currency_rates}

            rows: list[str] = []
            rows.append(
                "<tr style=\"background:#f5f5f5;\">"
                "<th style=\"padding:8px 12px;text-align:left;font-size:12px;\">币种</th>"
                "<th style=\"padding:8px 12px;text-align:right;font-size:12px;\">中间价</th>"
                "<th style=\"padding:8px 12px;text-align:right;font-size:12px;\">现汇买入</th>"
                "<th style=\"padding:8px 12px;text-align:right;font-size:12px;\">现汇卖出</th>"
                "</tr>"
            )

            for code in currencies:
                item = code_map.get(code)
                display_name = currency_names.get(code) or (item.get("name") if item else "") or code
                zhesuan = _format_optional(item.get("zhesuan"), price_precision) if item else "—"
                hui_in = _format_optional(item.get("hui_in"), price_precision) if item else "—"
                hui_out = _format_optional(item.get("hui_out"), price_precision) if item else "—"
                rows.append(
                    f"<tr style=\"border-bottom:1px solid #eee;\">"
                    f"<td style=\"padding:8px 12px;font-size:12px;\">{display_name}</td>"
                    f"<td style=\"padding:8px 12px;text-align:right;font-size:12px;\">{zhesuan}</td>"
                    f"<td style=\"padding:8px 12px;text-align:right;font-size:12px;\">{hui_in}</td>"
                    f"<td style=\"padding:8px 12px;text-align:right;font-size:12px;\">{hui_out}</td>"
                    "</tr>"
                )

            blocks.append(
                "<table style=\"width:100%;border-collapse:collapse;border:1px solid #eee;"
                "border-radius:6px;background:#fafafa;margin-bottom:12px;\">"
                + "".join(rows)
                + "</table>"
            )

        body = (
            "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "font-size:14px;line-height:1.6;\">"
            + "".join(blocks)
            + "</div>"
        )
        return [
            PushMessage(
                title=f"银行汇率简报 {date_str}",
                body=body,
                format="html",
                target_recipient=None,
            )
        ]
