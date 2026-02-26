"""PushPlus channel implementation (spec 6)."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

from src.models import PushMessage

logger = logging.getLogger(__name__)

PUSHPLUS_URL = "https://www.pushplus.plus/send"

# Match ${VAR_NAME} in token string
ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_token(raw: str) -> str:
    """Replace ${ENV_VAR} in token with os.environ values."""
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return os.environ.get(key, match.group(0))
    return ENV_PLACEHOLDER_RE.sub(repl, raw)


class PushPlusChannel:
    """PushPlus channel: POST to PushPlus API with token/title/content/template (spec 6.2)."""

    def send(self, msg: PushMessage, channel_config: dict) -> None:
        token = channel_config.get("token")
        if not token:
            logger.error("PushPlus channel_config missing 'token'")
            return
        token = _resolve_token(token)
        if not token:
            logger.error("PushPlus token is empty (env var not set or empty). Check .env and run after source .env")
            return
        mask = f"{token[:4]}***" if len(token) > 4 else "***"
        logger.info("PushPlus token: length=%s prefix=%s (check .env matches)", len(token), mask)
        title = msg.title
        content = msg.body
        template = "markdown" if msg.format == "markdown" else "txt"
        payload: dict[str, Any] = {
            "token": token,
            "title": title,
            "content": content,
            "template": template,
        }
        topic = channel_config.get("topic")
        if topic:
            payload["topic"] = topic

        try:
            resp = requests.post(PUSHPLUS_URL, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error(
                    "PushPlus send failed: status=%s body=%s",
                    resp.status_code,
                    resp.text[:500],
                )
                return
            data = resp.json()
            if isinstance(data, dict) and data.get("code") != 200:
                logger.error(
                    "PushPlus API error: code=%s msg=%s",
                    data.get("code"),
                    data.get("msg", ""),
                )
        except requests.RequestException as e:
            logger.exception("PushPlus request failed: %s", e)
