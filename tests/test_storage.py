import pytest

from job_search_automation.models import Vacancy
from job_search_automation.storage import ApplicationStateError, VacancyStore


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


def test_application_status_prevents_duplicate_or_ambiguous_retry(tmp_path) -> None:
    with VacancyStore(tmp_path / "jobs.db") as store:
        store.save([sample()])
        store.record_inspection("hh", "123", "https://example.test/form")
        store.record_review("hh", "123", "review-1", "review.json", "review.png")
        assert store.application("hh", "123").status == "review_ready"

        store.record_attempt("hh", "123", "review-1")
        with pytest.raises(ApplicationStateError):
            store.record_attempt("hh", "123", "review-1")
        with pytest.raises(ApplicationStateError):
            store.record_inspection("hh", "123", "https://example.test/form")

        store.record_submitted("hh", "123", "submission.json")
        assert store.application("hh", "123").status == "submitted"
