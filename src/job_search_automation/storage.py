from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
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
CREATE TABLE IF NOT EXISTS review_candidates (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    feedback TEXT CHECK(feedback IN ('relevant', 'irrelevant')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_review_candidates_seen
    ON review_candidates(last_seen DESC);
"""

_GENERIC_TITLE_WORDS = {
    "build", "create", "develop", "need", "help", "with", "for", "the",
    "module", "plugin", "dashboard", "project", "website", "app",
    "разработать", "создать", "нужно", "помощь", "сделать", "для",
}


def _title_terms(title: str) -> set[str]:
    return {
        term for term in re.findall(r"\b\w{3,}\b", title.casefold())
        if term not in _GENERIC_TITLE_WORDS and not term.isdigit()
    }


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

    def save_review_candidates(self, candidates: Iterable[tuple[Vacancy, str]]) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.connection:
            for vacancy, reason in candidates:
                self.connection.execute(
                    """INSERT INTO review_candidates
                       (source, source_id, payload_json, reason, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source, source_id) DO UPDATE SET
                           payload_json = excluded.payload_json,
                           reason = excluded.reason,
                           last_seen = excluded.last_seen""",
                    (vacancy.source, vacancy.source_id,
                     json.dumps(vacancy.to_dict(), ensure_ascii=False), reason, now, now),
                )

    def review_candidates(self, limit: int = 100) -> list[tuple[Vacancy, str]]:
        rows = self.connection.execute(
            """SELECT c.payload_json, c.reason FROM review_candidates c
               WHERE c.feedback IS NULL AND NOT EXISTS (
                   SELECT 1 FROM vacancies v
                   WHERE v.source = c.source AND v.source_id = c.source_id
               ) ORDER BY c.last_seen DESC, c.source_id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        feedback = Counter()
        for rated in self.connection.execute(
            "SELECT payload_json, feedback FROM review_candidates WHERE feedback IS NOT NULL"
        ):
            title = Vacancy.from_dict(json.loads(rated["payload_json"])).title
            direction = 1 if rated["feedback"] == "relevant" else -1
            for term in _title_terms(title):
                feedback[term] += direction
        items = [(Vacancy.from_dict(json.loads(row["payload_json"])), row["reason"])
                 for row in rows]
        # Feedback only reorders the review queue. It never silently widens strict matches.
        return sorted(
            items,
            key=lambda pair: sum(feedback[term] for term in _title_terms(pair[0].title)),
            reverse=True,
        )

    def promoted_review_keys(self) -> set[tuple[str, str]]:
        rows = self.connection.execute(
            "SELECT source, source_id FROM review_candidates WHERE feedback = 'relevant'"
        )
        return {(row["source"], row["source_id"]) for row in rows}

    def freelance_feedback(self, source: str, source_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT feedback FROM review_candidates WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        return row["feedback"] if row else None

    def rate_freelance(self, source: str, source_id: str, relevant: bool) -> bool:
        vacancy = self.get_vacancy(source, source_id)
        if vacancy is None or vacancy.kind != "freelance":
            return False
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.connection:
            self.connection.execute(
                """INSERT INTO review_candidates
                   (source, source_id, payload_json, reason, feedback, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, source_id) DO UPDATE SET
                       payload_json = excluded.payload_json,
                       feedback = excluded.feedback,
                       last_seen = excluded.last_seen""",
                (source, source_id, json.dumps(vacancy.to_dict(), ensure_ascii=False),
                 "accepted match", "relevant" if relevant else "irrelevant", now, now),
            )
        return True

    def rank_freelance(self, vacancies: Iterable[Vacancy]) -> list[Vacancy]:
        weights: Counter[str] = Counter()
        for row in self.connection.execute(
            "SELECT payload_json, feedback FROM review_candidates WHERE feedback IS NOT NULL"
        ):
            rated = Vacancy.from_dict(json.loads(row["payload_json"]))
            if rated.kind != "freelance":
                continue
            direction = 1 if row["feedback"] == "relevant" else -1
            for term in _title_terms(rated.title):
                weights[term] += direction

        def key(vacancy: Vacancy) -> tuple[int, float]:
            score = sum(weights[term] for term in _title_terms(vacancy.title))
            try:
                published = datetime.fromisoformat(vacancy.published_at)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=UTC)
                timestamp = published.timestamp()
            except ValueError:
                timestamp = 0.0
            return score, timestamp

        return sorted(vacancies, key=key, reverse=True)

    def rate_review_candidate(self, source: str, source_id: str, relevant: bool) -> bool:
        row = self.connection.execute(
            "SELECT payload_json FROM review_candidates WHERE source = ? AND source_id = ? "
            "AND feedback IS NULL", (source, source_id)
        ).fetchone()
        if row is None:
            return False
        if relevant:
            self.save([Vacancy.from_dict(json.loads(row["payload_json"]))])
        with self.connection:
            self.connection.execute(
                "UPDATE review_candidates SET feedback = ? WHERE source = ? AND source_id = ?",
                ("relevant" if relevant else "irrelevant", source, source_id),
            )
        return True

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
