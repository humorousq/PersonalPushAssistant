"""Runner: load config, match schedules by cron or --schedule, run jobs and send (spec 5.2)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from croniter import croniter

from src.channel import get_channel
from src.models import PluginContext, PushMessage
from src.plugins import get_plugin

logger = logging.getLogger(__name__)


def load_config(path: str | Path) -> dict:
    """Load YAML config from path."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_config(config: dict) -> None:
    """Validate recipients, schedules, jobs, plugin_configs; raise on error."""
    recipients = config.get("recipients") or {}
    schedules = config.get("schedules") or []
    plugin_configs = config.get("plugin_configs") or {}

    if not recipients:
        raise ValueError("config: recipients is empty")
    if not isinstance(recipients, dict):
        raise ValueError("config: recipients must be a dict")

    seen_schedule_ids = set()
    for i, sch in enumerate(schedules):
        if not isinstance(sch, dict):
            raise ValueError(f"config: schedules[{i}] must be a dict")
        sid = sch.get("id")
        if not sid:
            raise ValueError(f"config: schedules[{i}] missing 'id'")
        if sid in seen_schedule_ids:
            raise ValueError(f"config: duplicate schedule id '{sid}'")
        seen_schedule_ids.add(sid)
        jobs = sch.get("jobs") or []
        for j, job in enumerate(jobs):
            if not isinstance(job, dict):
                raise ValueError(f"config: schedules[{i}].jobs[{j}] must be a dict")
            rid = job.get("recipient_id")
            if rid not in recipients:
                raise ValueError(f"config: job recipient_id '{rid}' not in recipients")
            plugin_id = job.get("plugin_id")
            if not plugin_id:
                raise ValueError(f"config: job missing 'plugin_id'")
            try:
                get_plugin(plugin_id)
            except KeyError:
                raise ValueError(f"config: plugin_id '{plugin_id}' not in plugin registry")
            config_ref = job.get("config_ref")
            if not config_ref:
                raise ValueError(f"config: job missing 'config_ref'")
            if config_ref not in plugin_configs:
                raise ValueError(f"config: config_ref '{config_ref}' not in plugin_configs")


def schedules_to_run(config: dict, now: datetime, schedule_id: str | None) -> list[dict]:
    """Return list of schedule dicts to run: either [schedule with id] or cron-matched."""
    schedules = config.get("schedules") or []
    if schedule_id is not None:
        for sch in schedules:
            if sch.get("id") == schedule_id:
                return [sch]
        raise ValueError(f"schedule id '{schedule_id}' not found in config")

    now_utc = now.astimezone(timezone.utc)
    now_trunc = now_utc.replace(second=0, microsecond=0)
    base = now_trunc - timedelta(minutes=1)
    to_run = []
    for sch in schedules:
        cron_expr = sch.get("cron")
        if not cron_expr:
            continue
        try:
            it = croniter(cron_expr, base)
            next_run = it.get_next(datetime)
            next_trunc = next_run.replace(second=0, microsecond=0)
            if next_run.tzinfo is None:
                next_trunc = next_trunc.replace(tzinfo=timezone.utc)
            if next_trunc == now_trunc:
                to_run.append(sch)
        except Exception as e:
            logger.warning("cron parse/next failed for schedule %s: %s", sch.get("id"), e)
    return to_run


def run(config_path: str | Path, schedule_id: str | None = None, dry_run: bool = False) -> None:
    """Load config, validate, run matched schedules and send messages.

    If dry_run is True, plugins are executed and messages are generated,
    but no channel.send(...) is called; messages are only logged.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")

    config = load_config(path)
    validate_config(config)

    recipients = config.get("recipients") or {}
    plugin_configs = config.get("plugin_configs") or {}
    global_config = config.get("global_config") or {}

    now = datetime.now(timezone.utc)
    schedules = schedules_to_run(config, now, schedule_id)
    if not schedules:
        logger.info("No schedules to run (current time does not match any cron). Use --schedule <id> to run a schedule anyway.")
        return
    if dry_run:
        logger.info(
            "Dry-run mode enabled: will execute plugins but not send any messages to channels."
        )
    logger.info("Running %s schedule(s): %s", len(schedules), [s.get("id") for s in schedules])

    for sch in schedules:
        sid = sch.get("id", "?")
        jobs = sch.get("jobs") or []
        for job in jobs:
            recipient_id = job["recipient_id"]
            plugin_id = job["plugin_id"]
            config_ref = job["config_ref"]
            try:
                plugin_config = plugin_configs.get(config_ref) or {}
                plugin_cls = get_plugin(plugin_id)
                ctx = PluginContext(
                    now=now,
                    recipient_id=recipient_id,
                    plugin_config=plugin_config,
                    global_config=global_config,
                )
                plugin = plugin_cls()
                messages = plugin.run(ctx)
                channel_cfg = recipients[recipient_id].get("channel") or {}
                chan_type = channel_cfg.get("type", "pushplus")
                channel_cls = get_channel(chan_type)
                channel = channel_cls()
                for msg in messages:
                    m = msg
                    if m.target_recipient is None:
                        m = PushMessage(
                            title=m.title,
                            body=m.body,
                            format=m.format,
                            target_recipient=recipient_id,
                            priority=m.priority,
                            tags=m.tags,
                        )
                    target = m.target_recipient or recipient_id
                    if target not in recipients:
                        logger.warning("message target_recipient '%s' not in recipients, skip", target)
                        continue
                    if dry_run:
                        preview = (m.body or "")[:200].replace("\n", " ")
                        logger.info(
                            "Dry-run: would send to recipient='%s' via channel='%s' title=%r preview=%r",
                            target,
                            chan_type,
                            m.title,
                            preview,
                        )
                        continue
                    send_cfg = recipients[target].get("channel") or {}
                    channel.send(m, send_cfg)
            except Exception as e:
                logger.exception("job failed schedule=%s recipient=%s plugin=%s: %s", sid, recipient_id, plugin_id, e)
