from __future__ import annotations

import json
import os
import re
import string
from dataclasses import dataclass, replace
from pathlib import Path

from job_search_automation.models import Vacancy
from job_search_automation.profile import CandidateProfile


class TemplateError(ValueError):
    """Raised when a local reply template is invalid."""


DEFAULT_BODY = (
    "Здравствуйте!\n\n"
    "Меня зовут {name}. Хочу откликнуться на вакансию «{title}» в {company}.\n"
    "{about}\n{skills_line}\n{resume_line}\n{portfolio_line}\n{contact_line}\n\n"
    "С удовольствием отвечу на вопросы."
)
FIELDS = frozenset(
    {
        "name",
        "title",
        "company",
        "about",
        "skills_line",
        "resume_line",
        "portfolio_line",
        "contact_line",
        "about_en",
        "experience_line",
        "education_line",
        "languages_line",
        "availability_line",
        "rate_line",
    }
)


@dataclass(frozen=True, slots=True)
class ReplyTemplate:
    key: str
    name: str
    keywords: tuple[str, ...]
    body: str
    form_values: tuple[tuple[str, str], ...] = ()


DEFAULT_TEMPLATES = (
    ReplyTemplate(
        "freelance_ru",
        "Фриланс · RU",
        (),
        "Здравствуйте! Меня зовут {name}. Заинтересовала задача «{title}».\n"
        "{about}\n{skills_line}\n{portfolio_line}\n{contact_line}\n\n"
        "Готов обсудить объём, сроки и бюджет.",
    ),
    ReplyTemplate(
        "freelance_global",
        "Freelance · global",
        (),
        "Hello, I'm {name}. I'm interested in the project “{title}”.\n"
        "{about_en}\n{skills_line}\n{portfolio_line}\n{contact_line}\n\n"
        "I'd be glad to discuss the scope, timeline and budget.",
    ),
    ReplyTemplate(
        "job_global",
        "Job · global",
        (),
        "Hello, I'm {name}. I'm interested in the {title} role at {company}.\n"
        "{about_en}\n{skills_line}\n{resume_line}\n{portfolio_line}\n{contact_line}",
    ),
    ReplyTemplate(
        "internship",
        "Стажировка",
        ("стажёр", "стажер", "стажировка", "intern", "trainee"),
        DEFAULT_BODY,
    ),
    ReplyTemplate("python", "Python", ("python", "питон", "django", "fastapi"), DEFAULT_BODY),
    ReplyTemplate("analytics", "Аналитика", ("аналитик", "analyst", "analytics"), DEFAULT_BODY),
    ReplyTemplate("general", "Общий", (), DEFAULT_BODY),
)


def templates_path(database_path: Path) -> Path:
    return database_path.parent / "reply-templates.json"


def validate_body(body: str) -> None:
    if not body.strip() or len(body) > 2000:
        raise TemplateError("Текст шаблона должен содержать от 1 до 2000 символов")
    try:
        for _, field, format_spec, conversion in string.Formatter().parse(body):
            if field is not None and (field not in FIELDS or format_spec or conversion):
                raise TemplateError(f"Недопустимое поле {{{field}}} в шаблоне")
    except ValueError as exc:
        raise TemplateError("Неверный формат фигурных скобок в шаблоне") from exc


def load_templates(path: Path) -> tuple[ReplyTemplate, ...]:
    if not path.is_file():
        return DEFAULT_TEMPLATES
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TemplateError(f"Не удалось прочитать шаблоны: {exc}") from exc
    known = {item.key for item in DEFAULT_TEMPLATES}
    if not isinstance(raw, dict) or set(raw) - known:
        raise TemplateError("Файл шаблонов содержит неизвестный или отсутствующий раздел")
    result = []
    for default in DEFAULT_TEMPLATES:
        if default.key not in raw:
            result.append(default)
            continue
        value = raw[default.key]
        if not isinstance(value, dict):
            raise TemplateError(f"Неверный шаблон: {default.name}")
        body = value.get("body")
        keywords = value.get("keywords")
        if (
            not isinstance(body, str)
            or not isinstance(keywords, list)
            or any(not isinstance(word, str) for word in keywords)
        ):
            raise TemplateError(f"Неверный шаблон: {default.name}")
        validate_body(body)
        form_values = value.get("form_values", {})
        if not isinstance(form_values, dict) or any(
            not isinstance(key, str)
            or not isinstance(answer, str)
            or not key.strip()
            or len(key) > 60
            or len(answer) > 400
            for key, answer in form_values.items()
        ):
            raise TemplateError(f"Неверные значения полей анкеты: {default.name}")
        if (
            len(keywords) > 20
            or any(not word.strip() or len(word) > 40 for word in keywords)
            or (default.key == "general" and keywords)
        ):
            raise TemplateError(f"Неверные слова для шаблона: {default.name}")
        result.append(
            replace(
                default,
                body=body,
                keywords=tuple(keywords),
                form_values=tuple(form_values.items()),
            )
        )
    return tuple(result)


def save_template_field(path: Path, key: str, field: str, text: str) -> None:
    if field not in {"body", "keywords", "form_value"} or key not in {
        item.key for item in DEFAULT_TEMPLATES
    }:
        raise TemplateError("Неизвестное поле шаблона")
    if key == "general" and field == "keywords":
        raise TemplateError("Общий шаблон используется, когда другие не подошли")
    templates = list(load_templates(path))
    index = next(index for index, item in enumerate(templates) if item.key == key)
    if field == "body":
        validate_body(text)
        templates[index] = replace(templates[index], body=text.strip())
    elif field == "keywords":
        keywords = (
            ()
            if text.strip() == "-"
            else tuple(word.strip() for word in re.split(r"[,\n]", text) if word.strip())
        )
        if len(keywords) > 20 or any(len(word) > 40 for word in keywords):
            raise TemplateError("Можно указать не больше 20 слов длиной до 40 символов")
        templates[index] = replace(templates[index], keywords=keywords)
    else:
        name, separator, answer = text.partition("=")
        name, answer = name.strip(), answer.strip()
        if not separator or not name or len(name) > 60 or len(answer) > 400:
            raise TemplateError("Отправьте «Название поля = значение»")
        values = dict(templates[index].form_values)
        if answer == "-":
            values.pop(name, None)
        elif answer:
            values[name] = answer
        else:
            raise TemplateError("Значение поля пустое")
        if len(values) > 20:
            raise TemplateError("В шаблоне слишком много полей анкеты")
        templates[index] = replace(templates[index], form_values=tuple(values.items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            {
                item.key: {
                    "keywords": item.keywords,
                    "body": item.body,
                    "form_values": dict(item.form_values),
                }
                for item in templates
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def select_template(vacancy: Vacancy, templates: tuple[ReplyTemplate, ...]) -> ReplyTemplate:
    index = {item.key: item for item in templates}
    if vacancy.kind == "freelance":
        return index["freelance_ru" if vacancy.market == "ru" else "freelance_global"]
    if vacancy.market != "ru":
        return index["job_global"]
    title = vacancy.title.casefold().replace("ё", "е")
    for template in templates:
        if template.key not in {"freelance_ru", "freelance_global", "job_global"} and any(
            word.casefold().replace("ё", "е") in title for word in template.keywords
        ):
            return template
    return next(item for item in templates if item.key == "general")


def render_reply(profile: CandidateProfile, vacancy: Vacancy, template: ReplyTemplate) -> str:
    values = {
        "name": profile.name,
        "title": vacancy.title,
        "company": vacancy.company,
        "about": profile.about,
        "about_en": profile.about_en or profile.about,
        "experience_line": f"Experience: {profile.experience}" if profile.experience else "",
        "education_line": f"Education: {profile.education}" if profile.education else "",
        "languages_line": f"Languages: {profile.languages}" if profile.languages else "",
        "availability_line": f"Availability: {profile.availability}" if profile.availability else "",
        "rate_line": f"Rate: {profile.rate}" if profile.rate else "",
        "skills_line": f"Мои навыки: {', '.join(profile.skills)}." if profile.skills else "",
        "resume_line": f"Резюме: {profile.resume_url}" if profile.resume_url else "",
        "portfolio_line": f"Портфолио: {profile.portfolio_url}" if profile.portfolio_url else "",
        "contact_line": f"Связаться со мной: {profile.contact}",
    }
    lines = []
    for line in template.body.splitlines():
        rendered_line = line.format_map(values)
        if (
            line.strip()
            in {
                "{skills_line}",
                "{resume_line}",
                "{portfolio_line}",
                "{experience_line}",
                "{education_line}",
                "{languages_line}",
                "{availability_line}",
                "{rate_line}",
            }
            and not rendered_line
        ):
            continue
        lines.append(rendered_line)
    rendered = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(rendered) > 2700:
        raise TemplateError("Готовый отклик слишком длинный для карточки вакансии")
    return rendered
