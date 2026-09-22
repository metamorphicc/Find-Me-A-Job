from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass as dc
from typing import Any


@dc(frozen=True, slots=True)
class Vacancy:
    source: str
    source_id: str
    title: str
    company: str
    url: str
    area: str
    published_at: str
    work_formats: tuple[str, ...]
    experience: str
    employment: str
    salary_from: int | None
    salary_to: int | None
    salary_currency: str | None
    salary_gross: bool | None
    summary: str
    query: str

    def is_remote(self) -> bool:
        return "REMOTE" in self.work_formats

    def has_non_remote_format(self) -> bool:
        return bool({"ON_SITE", "HYBRID", "FIELD_WORK"}.intersection(self.work_formats))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["work_formats"] = list(self.work_formats)
        value["is_remote"] = self.is_remote()
        return value
