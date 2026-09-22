from __future__ import annotations

import html
import json
import os
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


def _shorten(value: str, limit: int = 260) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else f"{value[: limit - 1].rstrip()}…"


def _html_report(
    vacancies: list[Vacancy],
    *,
    created_at: datetime,
    fetched_count: int,
    rejected_count: int,
    transport: str,
    tokens_href: str,
    stylesheet_href: str,
) -> str:
    rows: list[str] = []
    for index, vacancy in enumerate(vacancies, start=1):
        title = html.escape(vacancy.title)
        company = html.escape(vacancy.company)
        area = html.escape(vacancy.area)
        experience = html.escape(vacancy.experience)
        salary = html.escape(_salary(vacancy))
        query = html.escape(vacancy.query)
        summary = html.escape(_shorten(vacancy.summary))
        url = html.escape(vacancy.url, quote=True)
        rows.append(
            f"""
            <li class="job-row">
              <div class="job-number" aria-hidden="true">{index:02d}</div>
              <article class="job-body">
                <h2>{title}</h2>
                <p class="job-company">{company} · {area}</p>
                <p class="job-summary">{summary or "Краткое описание отсутствует."}</p>
                <dl class="job-facts">
                  <div><dt>Опыт</dt><dd>{experience}</dd></div>
                  <div><dt>Зарплата</dt><dd>{salary}</dd></div>
                  <div><dt>Запрос</dt><dd>{query}</dd></div>
                </dl>
              </article>
              <a class="job-link" href="{url}" target="_blank" rel="noopener noreferrer">
                Открыть вакансию <span aria-hidden="true">↗</span>
              </a>
            </li>
            """
        )

    if not rows:
        rows.append(
            """
            <li class="empty-state">
              <p>Сегодня новых вакансий по заданным критериям нет.</p>
              <p>Следующий запуск снова проверит источники и покажет только новые позиции.</p>
            </li>
            """
        )

    created = html.escape(created_at.strftime("%d.%m.%Y · %H:%M"))
    transport_label = "Chromium" if transport == "browser" else "HeadHunter API"
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Новые удалённые вакансии · {created}</title>
  <link rel="stylesheet" href="{html.escape(tokens_href, quote=True)}">
  <link rel="stylesheet" href="{html.escape(stylesheet_href, quote=True)}">
</head>
<body>
  <header class="topline">
    <a class="wordmark" href="#top">Remote / новые</a>
    <a class="jump-link" href="#vacancies">К списку ↓</a>
  </header>

  <main id="top" class="page-shell">
    <section class="report-head" aria-labelledby="report-title">
      <p class="dateline">{created} · Новосибирск</p>
      <h1 id="report-title">Удалённая работа.<br>Свежая выборка.</h1>
      <p class="lede">Только новые вакансии, которые явно разрешают работу из дома. Уже просмотренные позиции остаются в локальной истории и не повторяются.</p>
    </section>

    <section class="scan-summary" aria-label="Сводка сканирования">
      <div><strong>{len(vacancies)}</strong><span>новых</span></div>
      <div><strong>{fetched_count}</strong><span>получено</span></div>
      <div><strong>{rejected_count}</strong><span>отсеяно</span></div>
    </section>

    <section id="vacancies" class="vacancy-index" aria-labelledby="vacancy-title">
      <div class="index-heading">
        <h2 id="vacancy-title">Вакансии</h2>
        <p>{len(vacancies)} позиций для просмотра</p>
      </div>
      <ol class="job-list">
        {"".join(rows)}
      </ol>
    </section>
  </main>

  <footer class="colophon">
    <p>Источник: HeadHunter · транспорт: {transport_label} · фильтр: только удалённо · отчёт хранится локально · создано {created}.</p>
  </footer>
</body>
</html>
"""


def write_report(
    reports_dir: str | Path,
    vacancies: list[Vacancy],
    *,
    fetched_count: int,
    rejected_count: int,
    transport: str,
    assets_root: str | Path,
) -> tuple[Path, Path, Path]:
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    stem = now.strftime("%Y%m%d-%H%M%S")
    markdown_path = directory / f"{stem}.md"
    json_path = directory / f"{stem}.json"
    html_path = directory / f"{stem}.html"

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
    stylesheet = Path(assets_root).resolve() / "report.css"
    tokens = Path(assets_root).resolve() / "tokens.css"
    stylesheet_href = Path(os.path.relpath(stylesheet, html_path.parent.resolve())).as_posix()
    tokens_href = Path(os.path.relpath(tokens, html_path.parent.resolve())).as_posix()
    html_path.write_text(
        _html_report(
            vacancies,
            created_at=now,
            fetched_count=fetched_count,
            rejected_count=rejected_count,
            transport=transport,
            tokens_href=tokens_href,
            stylesheet_href=stylesheet_href,
        ),
        encoding="utf-8",
    )
    return markdown_path, json_path, html_path
