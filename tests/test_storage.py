from dataclasses import replace

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


def test_review_feedback_reorders_only_pending_candidates(tmp_path) -> None:
    with VacancyStore(tmp_path / "jobs.db") as store:
        items = [
            replace(sample(), source="freelancer", source_id=str(index),
                    title=title, kind="freelance", categories=("software",))
            for index, title in enumerate(
                ("Build CRM module", "Create CRM plugin", "Build CRM dashboard",
                 "Create inventory dashboard"), start=1
            )
        ]
        store.save_review_candidates((item, "stack missing") for item in items)
        assert store.rate_review_candidate("freelancer", "1", relevant=True)
        assert store.rate_review_candidate("freelancer", "2", relevant=True)
        assert [item.source_id for item, _ in store.review_candidates()] == ["3", "4"]
        assert store.rate_review_candidate("freelancer", "3", relevant=False)
        assert [item.source_id for item, _ in store.review_candidates()] == ["4"]


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
