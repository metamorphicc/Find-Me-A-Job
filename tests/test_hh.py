from playwright.sync_api import sync_playwright

from job_search_automation.config import HhConfig, SearchConfig
from job_search_automation.hh import HhClient, _card_salary


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "pages": 1,
            "items": [
                {
                    "id": "42",
                    "name": "Удалённый стажёр",
                    "alternate_url": "https://hh.ru/vacancy/42",
                    "employer": {"name": "Example"},
                    "area": {"name": "Москва"},
                    "work_format": [{"id": "REMOTE", "name": "Из дома"}],
                    "experience": {"name": "Нет опыта"},
                    "employment_form": {"name": "Стажировка"},
                    "snippet": {"requirement": "Python", "responsibility": "Разработка"},
                    "published_at": "2026-09-22T00:00:00+0300",
                }
            ],
        }


def test_search_requests_current_remote_work_format(monkeypatch) -> None:
    client = HhClient(
        HhConfig(
            base_url="https://api.hh.ru",
            user_agent="Test/1.0",
            timeout_seconds=1,
        )
    )
    captured: dict[str, object] = {}

    def fake_get(url, *, params, timeout):
        captured.update(url=url, params=params, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(client.session, "get", fake_get)
    search = SearchConfig(
        queries=("стажер",),
        excluded_keywords=(),
        area_ids=("113",),
        experience_ids=("noExperience",),
        remote_only=True,
        strict_remote=True,
        days=7,
        per_query=50,
    )

    results = client.search(search)

    assert len(results) == 1
    assert results[0].is_remote()
    assert ("work_format", "REMOTE") in captured["params"]
    assert ("area", "113") in captured["params"]


def test_category_search_uses_professional_roles_instead_of_query(monkeypatch) -> None:
    client = HhClient(HhConfig("https://api.hh.ru", "test", 1))
    captured = {}

    def fake_get(url, *, params, timeout):
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr(client.session, "get", fake_get)
    search = SearchConfig((), (), (), (), True, True, 7, 20, categories=("software",))
    result = client.search(search)

    assert not any(name == "text" for name, _ in captured["params"])
    assert ("professional_role", "96") in captured["params"]
    assert result[0].categories == ("software",)


def test_precise_hh_filters_are_sent_to_api(monkeypatch) -> None:
    client = HhClient(HhConfig("https://api.hh.ru", "test", 1))
    captured = {}

    def fake_get(url, *, params, timeout):
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr(client.session, "get", fake_get)
    search = SearchConfig(
        (), (), (), (), True, True, 7, 20,
        categories=("software",), role_ids=("96",),
        title_keywords=("Python",), employment_forms=("FULL",),
        work_schedules=("FIVE_ON_TWO_OFF",),
        salary_min=100000, salary_currency="RUR", salary_required=True,
    )
    client.search(search)

    params = captured["params"]
    assert ("text", '"Python"') in params
    assert ("search_field", "name") in params
    assert ("professional_role", "96") in params
    assert ("professional_role", "124") not in params
    assert ("employment_form", "FULL") in params
    assert ("work_schedule_by_days", "FIVE_ON_TWO_OFF") in params
    assert ("salary", 100000) in params
    assert ("currency", "RUR") in params
    assert ("label", "with_salary") in params


def test_title_phrases_are_combined_into_one_hh_request(monkeypatch) -> None:
    client = HhClient(HhConfig("https://api.hh.ru", "test", 1))
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append(params)
        return FakeResponse()

    monkeypatch.setattr(client.session, "get", fake_get)
    search = SearchConfig(
        (), (), (), (), True, True, 7, 20,
        categories=("software",),
        title_keywords=("Python developer", "разработчик"),
    )
    client.search(search)

    assert len(calls) == 1
    assert ("text", '"Python developer" OR "разработчик"') in calls[0]


def test_browser_card_salary_is_parsed_for_local_filtering() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(
                '<div id="range"><span>80 000 – 150 000 <data value="RUB">₽</data> '
                'за месяц, на руки<data value="80000">80 000</data>'
                '<data value="150000">150 000</data></span></div>'
                '<div id="upper"><span>до <data value="120000">120 000</data> '
                '<data value="RUB">₽</data> за месяц</span></div>'
            )
            assert _card_salary(page.locator("#range")) == (80000, 150000, "RUR", False)
            assert _card_salary(page.locator("#upper")) == (None, 120000, "RUR", None)
        finally:
            browser.close()
