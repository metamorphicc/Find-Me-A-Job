from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from job_search_automation.config import ConfigError, load_config
from job_search_automation.forms import (
    FormProbeError,
    inspect_tilda,
    select_form,
    validate_form_url,
)
from job_search_automation.hh import HhApiError
from job_search_automation.reporting import write_report
from job_search_automation.search import SearchError, scan_vacancies
from job_search_automation.storage import VacancyStore
from job_search_automation.telegram_bot import TelegramApiError, run_bot


def _scan(config_path: Path, *, open_report: bool) -> int:
    config = load_config(config_path)
    result = scan_vacancies(config)

    markdown, data, report_html = write_report(
        config.reports_dir,
        result.new_items,
        fetched_count=result.fetched_count,
        rejected_count=result.fetched_count - result.accepted_count,
        transport=result.transport,
        assets_root=config_path.resolve().parent,
    )
    if open_report:
        webbrowser.open(report_html.resolve().as_uri())
    print(
        json.dumps(
            {
                "fetched": result.fetched_count,
                "accepted": result.accepted_count,
                "new": len(result.new_items),
                "transport": result.transport,
                "errors": result.errors,
                "report": str(markdown),
                "report_html": str(report_html),
                "data": str(data),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _doctor(config_path: Path) -> int:
    config = load_config(config_path)
    checks: dict[str, str] = {
        "config": "ok",
        "database_parent": str(config.database_path.parent),
        "reports_dir": str(config.reports_dir),
    }
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        browser.close()
    checks["chromium"] = "ok"
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0


def _list(config_path: Path, limit: int) -> int:
    config = load_config(config_path)
    with VacancyStore(config.database_path) as store:
        rows = store.recent(limit)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def _probe(url: str, form_index: int | None) -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(validate_form_url(url), wait_until="domcontentloaded", timeout=30_000)
            probes = inspect_tilda(page)
            selected = select_form(probes, form_index)
            print(json.dumps(selected.to_dict(), ensure_ascii=False, indent=2))
        finally:
            browser.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local remote-job discovery")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    scan_parser = commands.add_parser("scan", help="Find new vacancies and create a report")
    scan_parser.add_argument("--open", action="store_true", help="Open the HTML report")
    commands.add_parser("doctor", help="Check configuration and local Chromium")
    list_parser = commands.add_parser("list", help="Show recently discovered vacancies")
    list_parser.add_argument("--limit", type=int, default=20)
    commands.add_parser("bot", help="Run the private Telegram bot until Ctrl+C")
    probe_parser = commands.add_parser(
        "form-probe", help="Inspect a live Tilda form without filling"
    )
    probe_parser.add_argument("url")
    probe_parser.add_argument("--form-index", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            return _scan(args.config, open_report=args.open)
        if args.command == "doctor":
            return _doctor(args.config)
        if args.command == "list":
            return _list(args.config, args.limit)
        if args.command == "bot":
            run_bot(load_config(args.config))
            return 0
        if args.command == "form-probe":
            return _probe(args.url, args.form_index)
    except KeyboardInterrupt:
        print("\nTelegram-бот остановлен.")
        return 0
    except PlaywrightError:
        print(
            "error: Chromium для Playwright не установлен. Запустите "
            "'.\\.venv\\Scripts\\python.exe -m playwright install chromium'.",
            file=sys.stderr,
        )
        return 2
    except (ConfigError, FormProbeError, HhApiError, SearchError, TelegramApiError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2
