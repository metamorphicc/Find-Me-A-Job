from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Self

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
    status TEXT NOT NULL DEFAULT 'discovered',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_vacancies_last_seen ON vacancies(last_seen DESC);
"""


class VacancyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

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
                exists = self.connection.execute(
                    "SELECT 1 FROM vacancies WHERE source = ? AND source_id = ?",
                    (vacancy.source, vacancy.source_id),
                ).fetchone()
                if exists is None:
                    new_items.append(vacancy)
                self.connection.execute(
                    """
                    INSERT INTO vacancies (
                        source, source_id, title, company, url, area, published_at,
                        payload_json, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, source_id) DO UPDATE SET
                        title = excluded.title,
                        company = excluded.company,
                        url = excluded.url,
                        area = excluded.area,
                        published_at = excluded.published_at,
                        payload_json = excluded.payload_json,
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
            FROM vacancies
            ORDER BY first_seen DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_vacancies(self, limit: int = 5, offset: int = 0) -> list[Vacancy]:
        rows = self.connection.execute(
            "SELECT payload_json FROM vacancies ORDER BY first_seen DESC, source_id DESC LIMIT ? OFFSET ?",
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
        row = self.connection.execute("SELECT COUNT(*) FROM vacancies").fetchone()
        return int(row[0])
