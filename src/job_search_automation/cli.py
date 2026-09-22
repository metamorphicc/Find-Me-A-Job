from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from job_search_automation.config import ConfigError, load_config
from job_search_automation.filters import rejection_reason
from job_search_automation.hh import HhApiError, HhClient
from job_search_automation.reporting import write_report
from job_search_automation.storage import VacancyStore


def _scan(config_path: Path) -> int:
    config = load_config(config_path)
    client = HhClient(config.hh)
    fetched = client.search(config.search)
    accepted = [item for item in fetched if rejection_reason(item, config.search) is None]

    with VacancyStore(config.database_path) as store:
        new_items = store.save(accepted)

    markdown, data = write_report(
        config.reports_dir,
        new_items,
        fetched_count=len(fetched),
        rejected_count=len(fetched) - len(accepted),
    )
    print(
        json.dumps(
            {
                "fetched": len(fetched),
                "accepted": len(accepted),
                "new": len(new_items),
                "transport": client.last_transport,
                "report": str(markdown),
                "data": str(data),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _list(config_path: Path, limit: int) -> int:
    config = load_config(config_path)
    with VacancyStore(config.database_path) as store:
        rows = store.recent(limit)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local remote-job discovery")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="Find new vacancies and create a report")
    list_parser = commands.add_parser("list", help="Show recently discovered vacancies")
    list_parser.add_argument("--limit", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            return _scan(args.config)
        if args.command == "list":
            return _list(args.config, args.limit)
    except (ConfigError, HhApiError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2
