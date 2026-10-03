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


def test_regular_order_feedback_ranks_future_matches_and_can_be_changed(tmp_path) -> None:
    crm = replace(sample(), source="freelancer", source_id="crm", kind="freelance",
                  title="CRM integration", published_at="2026-09-20T00:00:00+00:00")
    newer_crm = replace(crm, source_id="crm-new",
                        published_at="2026-09-22T00:00:00+00:00")
    parser = replace(crm, source_id="parser", title="Inventory parser",
                     published_at="2026-09-23T00:00:00+00:00")
    with VacancyStore(tmp_path / "jobs.db") as store:
        store.save([sample(), crm, newer_crm, parser])
        assert not store.rate_freelance("hh", "123", relevant=True)
        assert store.rate_freelance("freelancer", "crm", relevant=True)
        assert store.freelance_feedback("freelancer", "crm") == "relevant"
        assert [item.source_id for item in store.rank_freelance([parser, newer_crm])] == [
            "crm-new", "parser"
        ]
        assert store.rate_freelance("freelancer", "crm", relevant=False)
        assert store.freelance_feedback("freelancer", "crm") == "irrelevant"
        assert [item.source_id for item in store.rank_freelance([parser, newer_crm])] == [
            "parser", "crm-new"
        ]


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
