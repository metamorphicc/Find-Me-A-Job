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


def test_professional_category_excludes_unrelated_remote_jobs():
    from dataclasses import replace

    focused = replace(settings(), categories=("software", "it_ops"))
    bartender = replace(vacancy("REMOTE"), title="Бармен", categories=())
    developer = replace(vacancy("REMOTE"), categories=("software",))
    assert rejection_reason(bartender, focused) == "outside selected professional categories"
    assert rejection_reason(developer, focused) is None


def test_title_and_salary_filter_reject_unverified_matches():
    from dataclasses import replace

    focused = replace(
        settings(), title_keywords=("developer", "разработчик"),
        salary_min=100000, salary_currency="RUR",
    )
    base = vacancy("REMOTE")
    assert rejection_reason(replace(base, title="Бармен"), focused) == (
        "title does not contain a required phrase"
    )
    assert rejection_reason(base, focused) == "salary not specified"
    assert rejection_reason(replace(base, salary_to=90000, salary_currency="RUR"), focused) == (
        "salary below minimum"
    )
    assert rejection_reason(replace(base, salary_from=120000, salary_currency="RUR"), focused) is None


def test_explicit_hh_role_overrides_broad_category():
    from dataclasses import replace

    focused = replace(settings(), categories=("software",), role_ids=("113",))
    administrator = replace(
        vacancy("REMOTE"), categories=("it_ops",), professional_role_ids=("113",)
    )
    assert rejection_reason(administrator, focused) is None
    assert rejection_reason(replace(administrator, professional_role_ids=("96",)), focused) == (
        "outside selected HH roles"
    )
