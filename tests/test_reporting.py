from job_search_automation.models import Vacancy
from job_search_automation.reporting import write_report


def test_html_report_contains_safe_clickable_vacancy(tmp_path) -> None:
    vacancy = Vacancy(
        source="hh",
        source_id="7",
        title="Python <Developer>",
        company="Example & Co",
        url="https://example.test/vacancy/7?a=1&b=2",
        area="Москва",
        published_at="2026-09-22T00:00:00+0300",
        work_formats=("REMOTE",),
        experience="Нет опыта",
        employment="Полная занятость",
        salary_from=100_000,
        salary_to=150_000,
        salary_currency="RUR",
        salary_gross=False,
        summary="Полностью удалённая работа",
        query="python",
    )

    _, _, html_path = write_report(
        tmp_path / "reports",
        [vacancy],
        fetched_count=1,
        rejected_count=0,
        transport="browser",
        assets_root=tmp_path,
    )
    contents = html_path.read_text(encoding="utf-8")

    assert "Python &lt;Developer&gt;" in contents
    assert "Example &amp; Co" in contents
    assert 'href="https://example.test/vacancy/7?a=1&amp;b=2"' in contents
    assert "Открыть вакансию" in contents
