from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_search_automation.models import Vacancy


class ProfileError(ValueError):
    """Raised when a private candidate profile cannot make a complete reply."""


EDITABLE_FIELDS = (
    "name",
    "about",
    "contact",
    "skills",
    "resume_url",
    "portfolio_url",
    "email",
    "phone",
    "city",
)


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    name: str
    about: str
    contact: str
    skills: tuple[str, ...]
    resume_url: str
    portfolio_url: str
    email: str = ""
    phone: str = ""
    city: str = ""
    resume_path: str = ""

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
    raw = read_profile_fields(profile_path)
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
        email=_text(raw, "email"),
        phone=_text(raw, "phone"),
        city=_text(raw, "city"),
        resume_path=_text(raw, "resume_path"),
    )
    combined_length = sum(
        len(value)
        for value in (
            profile.name,
            profile.about,
            profile.contact,
            profile.resume_url,
            profile.portfolio_url,
            profile.email,
            profile.phone,
            profile.city,
            profile.resume_path,
            *profile.skills,
        )
    )
    if combined_length > 2200:
        raise ProfileError("Текст profile.json слишком длинный для одного сообщения Telegram")
    return profile


def read_profile_fields(path: str | Path) -> dict[str, Any]:
    profile_path = Path(path)
    if not profile_path.is_file():
        return {}
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"Не удалось прочитать {profile_path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError("profile.json должен содержать JSON-объект")
    return raw


def save_profile_field(path: str | Path, field: str, text: str) -> None:
    if field not in EDITABLE_FIELDS:
        raise ProfileError("Неизвестное поле профиля")
    value = text.strip()
    if field in {"name", "about", "contact"} and (not value or value == "-"):
        raise ProfileError("Это поле нужно заполнить для готового текста отклика")
    if (
        field in {"resume_url", "portfolio_url"}
        and value not in {"", "-"}
        and not value.startswith("https://")
    ):
        raise ProfileError("Ссылка должна начинаться с https://")
    if (
        field == "email"
        and value not in {"", "-"}
        and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)
    ):
        raise ProfileError("Укажите корректный адрес электронной почты")
    if field == "phone" and value not in {"", "-"} and not re.fullmatch(r"[+\d()\s-]{7,30}", value):
        raise ProfileError("Укажите телефон цифрами, можно с +, пробелами и скобками")
    if field == "skills":
        parsed: str | list[str] = (
            []
            if value == "-"
            else [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
        )
        if len(parsed) > 20:
            raise ProfileError("Можно указать не больше 20 навыков")
    else:
        parsed = "" if value == "-" else value
    if len(value) > (1200 if field == "about" else 400):
        raise ProfileError("Это значение слишком длинное для сообщения Telegram")
    raw = read_profile_fields(path)
    raw[field] = parsed
    total_length = sum(
        len(item) if isinstance(item, str) else sum(len(str(part)) for part in item)
        for key, item in raw.items()
        if key in EDITABLE_FIELDS and isinstance(item, (str, list))
    )
    if total_length > 2200:
        raise ProfileError("Профиль слишком длинный для одного сообщения Telegram")
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = profile_path.with_name(f"{profile_path.name}.tmp")
    temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, profile_path)


def resume_file(profile: CandidateProfile, profile_path: str | Path) -> Path:
    if not profile.resume_path:
        raise ProfileError("Укажите resume_path в локальном profile.json")
    path = Path(profile.resume_path)
    if not path.is_absolute():
        path = Path(profile_path).resolve().parent / path
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() not in {".pdf", ".doc", ".docx"}:
        raise ProfileError("Резюме не найдено или формат файла не поддерживается")
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ProfileError("Резюме должно быть не больше 10 МБ")
    return path
