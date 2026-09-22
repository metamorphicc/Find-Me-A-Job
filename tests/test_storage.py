from job_search_automation.models import Vacancy
from job_search_automation.storage import VacancyStore


def sample() -> Vacancy:
    return Vacancy(
        source="hh",
        source_id="123",
        title="Remote intern",
        company="Example",
        url="https://example.test/123",
        area="Москва",
        published_at="2026-09-22T00:00:00+0300",
        work_formats=("REMOTE",),
        experience="Нет опыта",
        employment="Стажировка",
        salary_from=None,
        salary_to=None,
        salary_currency=None,
        salary_gross=None,
        summary="",
        query="стажер",
    )


def test_existing_vacancy_is_not_reported_as_new_twice(tmp_path) -> None:
    with VacancyStore(tmp_path / "jobs.db") as store:
        assert store.save([sample()]) == [sample()]
        assert store.save([sample()]) == []
        assert len(store.recent()) == 1
