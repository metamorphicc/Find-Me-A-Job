from dataclasses import replace

import pytest

from job_search_automation.config import load_config
from job_search_automation.models import Vacancy
from job_search_automation.search import SearchError, scan_with_providers


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
    config = replace(config, search=replace(config.search, sources=("hh", "remote")))
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
