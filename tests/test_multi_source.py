import json
import sqlite3
from dataclasses import replace

import pytest

from job_search_automation.config import load_config
from job_search_automation.models import Vacancy
from job_search_automation.search import SearchError, scan_with_providers
from job_search_automation.storage import VacancyStore


class Provider:
    last_transport = "test"

    def __init__(self, items=(), error=None):
        self.items = list(items)
        self.error = error

    def search(self, _settings):
        if self.error:
            raise RuntimeError(self.error)
        return self.items


def vacancy(source):
    return Vacancy(
        source=source,
        source_id="1",
        title="Python developer",
        company="Example",
        url="https://example.test/job",
        area="Remote",
        published_at="2026-09-24T00:00:00Z",
        work_formats=("REMOTE",),
        experience="",
        employment="",
        salary_from=None,
        salary_to=None,
        salary_currency=None,
        salary_gross=None,
        summary="Python",
        query="Python",
    )


def test_scan_keeps_successful_sources_when_one_fails(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[search]\nqueries = ["Python"]\n', encoding="utf-8")
    config = load_config(config_path)
    config = replace(
        config, search=replace(config.search, sources=("hh", "remote"), categories=())
    )
    providers = {"hh": Provider(error="offline"), "remote": Provider([vacancy("remote")])}

    first = scan_with_providers(config, providers)
    second = scan_with_providers(config, providers)

    assert [item.source for item in first.new_items] == ["remote"]
    assert second.new_items == []
    assert first.errors == ("hh: offline",)


def test_scan_reports_all_failed_sources(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[search]\nqueries = ["Python"]\n', encoding="utf-8")
    config = load_config(config_path)
    with pytest.raises(SearchError, match="Все источники недоступны"):
        scan_with_providers(config, {"hh": Provider(error="offline")})


def test_old_stored_vacancy_loads_new_optional_fields():
    payload = vacancy("hh").to_dict()
    for field in ("kind", "market", "location_scope", "pay_label"):
        payload.pop(field)
    restored = Vacancy.from_dict(payload)
    assert restored.kind == "job"
    assert restored.market == "ru"


def test_cross_source_duplicate_is_kept_once_in_history(tmp_path):
    database = tmp_path / "jobs.db"
    with VacancyStore(database) as store:
        assert len(store.save([vacancy("hh")])) == 1
        assert store.save([vacancy("remotive")]) == []
        assert store.count() == 1
        assert store.get_vacancy("remotive", "1") is not None
        assert [item.source for item in store.recent_vacancies()] == ["hh"]


def test_existing_database_adds_cross_source_index_without_losing_rows(tmp_path):
    path = tmp_path / "jobs.db"
    item = vacancy("hh")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE vacancies (
                source TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL,
                company TEXT NOT NULL, url TEXT NOT NULL, area TEXT NOT NULL,
                published_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                status TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                PRIMARY KEY (source, source_id))"""
        )
        connection.execute(
            """INSERT INTO vacancies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.source, item.source_id, item.title, item.company, item.url, item.area,
                item.published_at, json.dumps(item.to_dict()), "discovered", "2026-09-24", "2026-09-24",
            ),
        )
    with VacancyStore(path) as store:
        assert store.count() == 1
        assert store.save([vacancy("wwr")]) == []
