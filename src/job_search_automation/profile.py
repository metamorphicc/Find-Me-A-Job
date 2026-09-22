from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_search_automation.models import Vacancy


class ProfileError(ValueError):
    """Raised when a private candidate profile cannot make a complete reply."""


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    name: str
    about: str
    contact: str
    skills: tuple[str, ...]
    resume_url: str
    portfolio_url: str

    def application_text(self, vacancy: Vacancy) -> str:
        lines = [
            "Здравствуйте!",
            "",
            (
                f"Меня зовут {self.name}. Хочу откликнуться на вакансию «{vacancy.title}» "
                f"в {vacancy.company}."
            ),
            self.about,
        ]
        if self.skills:
            lines.append(f"Мои навыки: {', '.join(self.skills)}.")
        if self.resume_url:
            lines.append(f"Резюме: {self.resume_url}")
        if self.portfolio_url:
            lines.append(f"Портфолио: {self.portfolio_url}")
        lines.extend(
            [f"Связаться со мной: {self.contact}", "", "С удовольствием отвечу на вопросы."]
        )
        return "\n".join(lines)


def _text(raw: dict[str, Any], key: str, *, required: bool = False) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise ProfileError(f"profile.{key} must be a string")
    value = value.strip()
    if required and (not value or value.startswith("REPLACE_WITH_")):
        raise ProfileError(f"Заполните поле {key} в локальном profile.json")
    return value


def load_profile(path: str | Path) -> CandidateProfile:
    profile_path = Path(path)
    if not profile_path.is_file():
        raise ProfileError(
            f"Не найден {profile_path.name}. Скопируйте profile.example.json в profile.json "
            "и заполните свои данные."
        )
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"Не удалось прочитать {profile_path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError("profile.json должен содержать JSON-объект")
    skills = raw.get("skills", [])
    if not isinstance(skills, list) or any(not isinstance(skill, str) for skill in skills):
        raise ProfileError("profile.skills должен быть списком строк")
    profile = CandidateProfile(
        name=_text(raw, "name", required=True),
        about=_text(raw, "about", required=True),
        contact=_text(raw, "contact", required=True),
        skills=tuple(skill.strip() for skill in skills if skill.strip()),
        resume_url=_text(raw, "resume_url"),
        portfolio_url=_text(raw, "portfolio_url"),
    )
    combined_length = sum(
        len(value)
        for value in (
            profile.name,
            profile.about,
            profile.contact,
            profile.resume_url,
            profile.portfolio_url,
            *profile.skills,
        )
    )
    if combined_length > 2200:
        raise ProfileError("Текст profile.json слишком длинный для одного сообщения Telegram")
    return profile
