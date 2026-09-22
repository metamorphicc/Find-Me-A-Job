from job_search_automation.config import HhConfig, SearchConfig
from job_search_automation.hh import HhClient


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
