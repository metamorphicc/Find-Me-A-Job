from job_search_automation.config import SearchConfig
from job_search_automation.filters import rejection_reason
from job_search_automation.models import Vacancy


def vacancy(*formats: str, summary: str = "") -> Vacancy:
    return Vacancy(
        source="hh",
        source_id="1",
        title="Junior Python developer",
        company="Example",
        url="https://example.test/1",
        area="Москва",
        published_at="2026-09-22T00:00:00+0300",
        work_formats=formats,
        experience="Нет опыта",
        employment="Полная занятость",
        salary_from=None,
        salary_to=None,
        salary_currency=None,
        salary_gross=None,
        summary=summary,
        query="junior",
    )


def settings() -> SearchConfig:
    return SearchConfig(
        queries=("junior",),
        excluded_keywords=("релокация",),
        area_ids=("113",),
        experience_ids=("noExperience",),
        remote_only=True,
        strict_remote=True,
        days=7,
        per_query=50,
    )


def test_accepts_remote_vacancy_even_when_employer_area_is_moscow() -> None:
    assert rejection_reason(vacancy("REMOTE"), settings()) is None


def test_rejects_vacancy_that_also_requires_hybrid_work() -> None:
    assert rejection_reason(vacancy("REMOTE", "HYBRID"), settings()) is not None


def test_rejects_excluded_terms() -> None:
    assert (
        rejection_reason(vacancy("REMOTE", summary="Требуется релокация"), settings()) is not None
    )


def test_can_search_only_jobs_or_only_freelance():
    from dataclasses import replace

    job_only = replace(settings(), kinds=("job",))
    assert rejection_reason(replace(vacancy("REMOTE"), kind="freelance"), job_only)
