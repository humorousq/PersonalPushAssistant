"""CLI entry: python -m src.cli run [--config path] [--schedule id] (spec 5.1)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.runner import run

load_dotenv()

DEFAULT_CONFIG = "config/config.yaml"
LOG_DIR = Path("logs")


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / "app.log"
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    formatter = logging.Formatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        h_stderr = logging.StreamHandler(sys.stderr)
        h_stderr.setFormatter(formatter)
        root.addHandler(h_stderr)
        h_file = logging.FileHandler(log_file, encoding="utf-8")
        h_file.setFormatter(formatter)
        root.addHandler(h_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Push Assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"Config file path (default: {DEFAULT_CONFIG})",
    )
    run_parser.add_argument(
        "--schedule",
        default=None,
        help="Run only this schedule id (default: match by cron)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute plugins and log messages but do not send to channels",
    )
    args = parser.parse_args()

    _setup_logging()

    if args.command == "run":
        try:
            run(args.config, args.schedule, args.dry_run)
        except FileNotFoundError as e:
            logging.error("%s", e)
            sys.exit(1)
        except ValueError as e:
            logging.error("%s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()
