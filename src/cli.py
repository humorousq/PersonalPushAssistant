"""CLI entry: python -m src.cli run [--config path] [--schedule id] (spec 5.1)."""
from __future__ import annotations

import argparse
import logging
import sys

from src.runner import run

DEFAULT_CONFIG = "config/config.yaml"


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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    if args.command == "run":
        try:
            run(args.config, args.schedule)
        except FileNotFoundError as e:
            logging.error("%s", e)
            sys.exit(1)
        except ValueError as e:
            logging.error("%s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()
