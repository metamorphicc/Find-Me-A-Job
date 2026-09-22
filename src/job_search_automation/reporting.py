from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from job_search_automation.models import Vacancy


def _salary(vacancy: Vacancy) -> str:
    if vacancy.salary_from is None and vacancy.salary_to is None:
        return "не указана"
    currency = vacancy.salary_currency or ""
    if vacancy.salary_from is not None and vacancy.salary_to is not None:
        value = f"{vacancy.salary_from:,}–{vacancy.salary_to:,} {currency}"
    elif vacancy.salary_from is not None:
        value = f"от {vacancy.salary_from:,} {currency}"
    else:
        value = f"до {vacancy.salary_to:,} {currency}"
    return value.replace(",", " ")


def write_report(
    reports_dir: str | Path,
    vacancies: list[Vacancy],
    *,
    fetched_count: int,
    rejected_count: int,
) -> tuple[Path, Path]:
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    stem = now.strftime("%Y%m%d-%H%M%S")
    markdown_path = directory / f"{stem}.md"
    json_path = directory / f"{stem}.json"

    lines = [
        "# Новые удалённые вакансии",
        "",
        f"Создано: {now.isoformat(timespec='minutes')}",
        "",
        f"Получено из источника: {fetched_count}",
        f"Отфильтровано: {rejected_count}",
        f"Новых: {len(vacancies)}",
        "",
    ]
    if not vacancies:
        lines.append("Новых вакансий по заданным критериям нет.")
    for index, vacancy in enumerate(vacancies, start=1):
        lines.extend(
            [
                f"## {index}. [{vacancy.title}]({vacancy.url})",
                "",
                f"- Компания: {vacancy.company}",
                f"- Регион: {vacancy.area}",
                f"- Опыт: {vacancy.experience}",
                f"- Занятость: {vacancy.employment}",
                f"- Зарплата: {_salary(vacancy)}",
                f"- Опубликована: {vacancy.published_at}",
                f"- Поисковый запрос: {vacancy.query}",
                "",
                vacancy.summary or "Описание в результатах поиска отсутствует.",
                "",
            ]
        )

    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(
        json.dumps([vacancy.to_dict() for vacancy in vacancies], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return markdown_path, json_path
