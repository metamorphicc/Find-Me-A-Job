from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Self

from job_search_automation.dedup import canonical_key
from job_search_automation.models import Vacancy

SCHEMA = """
CREATE TABLE IF NOT EXISTS vacancies (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    url TEXT NOT NULL,
    area TEXT NOT NULL,
    published_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    canonical_key TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_vacancies_last_seen ON vacancies(last_seen DESC);
CREATE TABLE IF NOT EXISTS applications (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    form_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('inspected', 'review_ready', 'attempted', 'submitted')),
    review_id TEXT,
    review_path TEXT,
    screenshot_path TEXT,
    submission_path TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
"""


class ApplicationStateError(ValueError):
    """Raised when an application cannot move to the requested state."""


@dataclass(frozen=True, slots=True)
class ApplicationRecord:
    source: str
    source_id: str
    form_url: str
    status: str
    review_id: str | None
    review_path: str | None
    screenshot_path: str | None
    submission_path: str | None
    updated_at: str


class VacancyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(vacancies)")}
        if "canonical_key" not in columns:
            self.connection.execute("ALTER TABLE vacancies ADD COLUMN canonical_key TEXT")
        rows = self.connection.execute(
            "SELECT source, source_id, payload_json FROM vacancies WHERE canonical_key IS NULL"
        ).fetchall()
        with self.connection:
            for row in rows:
                vacancy = Vacancy.from_dict(json.loads(row["payload_json"]))
                self.connection.execute(
                    "UPDATE vacancies SET canonical_key = ? WHERE source = ? AND source_id = ?",
                    (canonical_key(vacancy), row["source"], row["source_id"]),
                )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_vacancies_canonical ON vacancies(canonical_key)"
            )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save(self, vacancies: Iterable[Vacancy]) -> list[Vacancy]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        new_items: list[Vacancy] = []
        with self.connection:
            for vacancy in vacancies:
                key = canonical_key(vacancy)
                exists = self.connection.execute(
                    "SELECT 1 FROM vacancies WHERE source = ? AND source_id = ?",
                    (vacancy.source, vacancy.source_id),
                ).fetchone()
                duplicate = self.connection.execute(
                    """SELECT 1 FROM vacancies
                    WHERE canonical_key = ? AND source != ? AND status != 'duplicate'
                    LIMIT 1""",
                    (key, vacancy.source),
                ).fetchone()
                if exists is None and duplicate is None:
                    new_items.append(vacancy)
                self.connection.execute(
                    """
                    INSERT INTO vacancies (
                        source, source_id, title, company, url, area, published_at,
                        payload_json, canonical_key, status, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, source_id) DO UPDATE SET
                        title = excluded.title,
                        company = excluded.company,
                        url = excluded.url,
                        area = excluded.area,
                        published_at = excluded.published_at,
                        payload_json = excluded.payload_json,
                        canonical_key = excluded.canonical_key,
                        last_seen = excluded.last_seen
                    """,
                    (
                        vacancy.source,
                        vacancy.source_id,
                        vacancy.title,
                        vacancy.company,
                        vacancy.url,
                        vacancy.area,
                        vacancy.published_at,
                        json.dumps(vacancy.to_dict(), ensure_ascii=False),
                        key,
                        "duplicate" if duplicate is not None else "discovered",
                        now,
                        now,
                    ),
                )
        return new_items

    def recent(self, limit: int = 20) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT source, source_id, title, company, url, area, published_at,
                   status, first_seen, last_seen
            FROM vacancies WHERE status != 'duplicate'
            ORDER BY first_seen DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_vacancies(self, limit: int = 5, offset: int = 0) -> list[Vacancy]:
        rows = self.connection.execute(
            "SELECT payload_json FROM vacancies WHERE status != 'duplicate' "
            "ORDER BY first_seen DESC, source_id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [Vacancy.from_dict(json.loads(row["payload_json"])) for row in rows]

    def get_vacancy(self, source: str, source_id: str) -> Vacancy | None:
        row = self.connection.execute(
            "SELECT payload_json FROM vacancies WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        return Vacancy.from_dict(json.loads(row["payload_json"])) if row else None

    def count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM vacancies WHERE status != 'duplicate'"
        ).fetchone()
        return int(row[0])

    def application(self, source: str, source_id: str) -> ApplicationRecord | None:
        row = self.connection.execute(
            "SELECT * FROM applications WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        return ApplicationRecord(**dict(row)) if row else None

    def record_inspection(self, source: str, source_id: str, form_url: str) -> None:
        current = self.application(source, source_id)
        if current and current.status in {"attempted", "submitted"}:
            raise ApplicationStateError("Заявка уже была отправлена или попытка отправки неясна")
        if self.get_vacancy(source, source_id) is None:
            raise ApplicationStateError("Вакансия не найдена")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO applications (source, source_id, form_url, status, updated_at)
                VALUES (?, ?, ?, 'inspected', ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    form_url = excluded.form_url, status = 'inspected', review_id = NULL,
                    review_path = NULL, screenshot_path = NULL, updated_at = excluded.updated_at
                """,
                (source, source_id, form_url, now),
            )

    def record_review(
        self,
        source: str,
        source_id: str,
        review_id: str,
        review_path: str,
        screenshot_path: str,
    ) -> None:
        with self.connection:
            result = self.connection.execute(
                """
                UPDATE applications SET status = 'review_ready', review_id = ?,
                    review_path = ?, screenshot_path = ?, updated_at = ?
                WHERE source = ? AND source_id = ? AND status IN ('inspected', 'review_ready')
                """,
                (
                    review_id,
                    review_path,
                    screenshot_path,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    source,
                    source_id,
                ),
            )
            if result.rowcount != 1:
                raise ApplicationStateError("Форма не проверена или заявка уже обработана")

    def record_attempt(self, source: str, source_id: str, review_id: str) -> None:
        with self.connection:
            result = self.connection.execute(
                """
                UPDATE applications SET status = 'attempted', updated_at = ?
                WHERE source = ? AND source_id = ? AND status = 'review_ready' AND review_id = ?
                """,
                (
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    source,
                    source_id,
                    review_id,
                ),
            )
            if result.rowcount != 1:
                raise ApplicationStateError("Нет подтверждённой заявки или отправка уже началась")

    def record_submitted(self, source: str, source_id: str, submission_path: str) -> None:
        with self.connection:
            result = self.connection.execute(
                """
                UPDATE applications SET status = 'submitted', submission_path = ?, updated_at = ?
                WHERE source = ? AND source_id = ? AND status = 'attempted'
                """,
                (
                    submission_path,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    source,
                    source_id,
                ),
            )
            if result.rowcount != 1:
                raise ApplicationStateError("Попытка отправки не зарегистрирована")
