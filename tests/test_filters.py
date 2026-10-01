from dataclasses import replace

from job_search_automation.categories import (
    DEFAULT_EXCLUDED_TITLES,
    DEFAULT_STACK_SIGNALS,
    DEFAULT_TECH_TITLES,
)
from job_search_automation.config import SearchConfig, freelance_search_defaults
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


def test_stack_and_title_exclusions_keep_development_results_focused():
    focused = replace(
        settings(),
        categories=("software",),
        title_keywords=("backend", "react"),
        stack_keywords=("react", "typescript", "node.js"),
        excluded_title_keywords=("qa", "devops"),
    )
    base = replace(vacancy("REMOTE"), title="Backend developer", categories=("software",))
    assert rejection_reason(replace(base, summary="Java and Spring"), focused) == (
        "title and description do not mention the selected stack"
    )
    assert rejection_reason(replace(base, summary="TypeScript and Node.js"), focused) is None
    assert rejection_reason(replace(base, title="QA React engineer"), focused) == (
        "title contains an excluded role"
    )
    assert rejection_reason(replace(base, title="DevOps React engineer"), focused) == (
        "title contains an excluded role"
    )


def test_default_focus_requires_a_real_stack_signal_not_just_fullstack_title():
    focused = replace(
        settings(),
        categories=("software",),
        title_keywords=DEFAULT_TECH_TITLES,
        stack_keywords=DEFAULT_STACK_SIGNALS,
        excluded_title_keywords=DEFAULT_EXCLUDED_TITLES,
    )
    base = replace(vacancy("REMOTE"), categories=("software",))
    assert rejection_reason(replace(base, title="Senior Fullstack PHP Developer"), focused) == (
        "title and description do not mention the selected stack"
    )
    assert rejection_reason(replace(base, title="Автор резюме Go / Java / Python"), focused) == (
        "title does not contain a required phrase"
    )
    assert rejection_reason(replace(base, title="Senior Mobile Engineer (React Native)"), focused) == (
        "title contains an excluded role"
    )
    assert rejection_reason(replace(base, title="Backend TypeScript Developer"), focused) is None


def test_freelance_defaults_ignore_client_country_but_require_coding_signal():
    focused = freelance_search_defaults(settings())
    project = replace(
        vacancy("REMOTE"), source="freelancer", kind="freelance",
        title="Build a booking system", area="Brazil", location_scope="Brazil",
        summary="Use Python and PostgreSQL", categories=("software",),
    )
    assert focused.sources == ("freelancer", "freelancehunt")
    assert focused.kinds == ("freelance",)
    assert focused.area_ids == ()
    assert focused.title_keywords == ()
    assert rejection_reason(project, focused) is None
    assert rejection_reason(replace(project, title="Write paid email copy", summary=""), focused) == (
        "title and description do not mention the selected stack"
    )
    assert rejection_reason(
        replace(project, title="Map creation", summary="Project description included"), focused
    ) == "title and description do not mention the selected stack"
    assert rejection_reason(replace(project, work_formats=("ON_SITE",)), focused) == "not remote"


def test_freelance_skill_tags_need_implementation_context():
    focused = freelance_search_defaults(settings())
    base = replace(
        vacancy("REMOTE"), source="freelancer", kind="freelance",
        categories=("software",), summary="Project description.\nНавыки: Python",
    )
    assert rejection_reason(replace(base, title="Build booking system"), focused) is None
    assert rejection_reason(replace(base, title="Map creation"), focused) == (
        "title and description do not mention the selected stack"
    )
    assert rejection_reason(replace(base, title="WordPress API design"), focused) == (
        "title contains an excluded role"
    )


def test_project_budget_filter_keeps_currency_and_unit_separate():
    focused = replace(freelance_search_defaults(settings()), budget_min=500, budget_currency="USD")
    project = replace(
        vacancy("REMOTE"), source="freelancer", kind="freelance",
        title="Build Python API", categories=("software",),
        budget_max=600, budget_currency="USD", budget_unit="project",
    )
    assert rejection_reason(project, focused) is None
    assert rejection_reason(replace(project, budget_max=400), focused) == (
        "project budget below minimum"
    )
    assert rejection_reason(replace(project, budget_currency="UAH"), focused) == (
        "project budget currency differs"
    )
    assert rejection_reason(replace(project, budget_unit="hour"), focused) == (
        "hourly rate is not a project budget"
    )
    assert rejection_reason(replace(project, budget_max=None), focused) == (
        "project budget not specified"
    )
