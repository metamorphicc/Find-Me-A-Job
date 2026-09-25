from __future__ import annotations

import html
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import Error as PlaywrightError

from job_search_automation.application_workflow import ApplicationManager, ReviewSummary
from job_search_automation.categories import CATEGORY_LABELS, HH_ROLE_LABELS
from job_search_automation.config import (
    AppConfig,
    ConfigError,
    SearchConfig,
    load_schedule_settings,
    load_search_settings,
    save_schedule_settings,
    save_search_settings,
    schedule_settings_path,
    search_settings_path,
    validate_schedule,
)
from job_search_automation.discover import discover_application_links
from job_search_automation.filters import rejection_reason
from job_search_automation.forms import FormProbeError
from job_search_automation.hh import HhApiError
from job_search_automation.models import Vacancy
from job_search_automation.profile import (
    EDITABLE_FIELDS,
    CandidateProfile,
    ProfileError,
    initialize_profile,
    load_profile,
    missing_reply_fields,
    read_profile_fields,
    save_custom_fact,
    save_profile_field,
)
from job_search_automation.reply_templates import (
    DEFAULT_TEMPLATES,
    ReplyTemplate,
    TemplateError,
    load_templates,
    render_reply,
    save_template_field,
    select_template,
    templates_path,
)
from job_search_automation.reporting import _salary
from job_search_automation.scheduling import DailyScheduler
from job_search_automation.search import ScanResult, SearchError, scan_vacancies
from job_search_automation.storage import ApplicationStateError, VacancyStore
from job_search_automation.tilda_apply import FillError

SEARCH_BUTTON = "🔎 Искать вакансии"
HISTORY_BUTTON = "📚 Ранее найденные"
SETTINGS_BUTTON = "⚙️ Настройки"
MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": SEARCH_BUTTON}, {"text": HISTORY_BUTTON}],
        [{"text": SETTINGS_BUTTON}],
    ],
    "resize_keyboard": True,
}

PROFILE_LABELS = {
    "name": "Имя",
    "about": "О себе",
    "contact": "Контакт",
    "skills": "Навыки",
    "resume_url": "Ссылка на резюме",
    "portfolio_url": "Ссылка на портфолио",
    "email": "Эл. почта для анкеты",
    "phone": "Телефон для анкеты",
    "city": "Город для анкеты",
    "experience": "Опыт",
    "education": "Образование",
    "languages": "Языки",
    "timezone": "Часовой пояс",
    "availability": "Когда готов начать",
    "work_authorization": "Право на работу",
    "rate": "Ставка / ожидания",
    "about_en": "About me (English)",
    "resume_path": "Путь к файлу резюме",
}

SOURCE_LABELS = {
    "hh": "HeadHunter",
    "superjob": "SuperJob (нужен ключ)",
    "remotive": "Remotive",
    "wwr": "We Work Remotely",
    "fl": "FL.ru RSS",
    "freelancer": "Freelancer.com",
}

EMPLOYMENT_LABELS = {
    "FULL": "Полная занятость",
    "PART": "Частичная занятость",
    "PROJECT": "Подработка",
    "FLY_IN_FLY_OUT": "Вахта",
}

SCHEDULE_LABELS = {
    "FIVE_ON_TWO_OFF": "5/2",
    "TWO_ON_TWO_OFF": "2/2",
    "FLEXIBLE": "Гибкий график",
    "WEEKEND": "По выходным",
}

EXPERIENCE_LABELS = {
    "noExperience": "без опыта",
    "between1And3": "1–3 года",
    "between3And6": "3–6 лет",
    "moreThan6": "более 6 лет",
}


class TelegramApiError(RuntimeError):
    """Raised when the Telegram Bot API cannot complete a request."""


class TelegramApi:
    def __init__(self, token: str) -> None:
        self.token = token
        self.session = requests.Session()

    def _request(self, method: str, payload: dict[str, Any], *, timeout: int = 20) -> Any:
        try:
            response = self.session.post(
                f"https://api.telegram.org/bot{self.token}/{method}",
                json=payload,
                timeout=timeout,
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise TelegramApiError("Не удалось связаться с Telegram Bot API") from exc
        if not isinstance(data, dict):
            raise TelegramApiError("Telegram Bot API вернул неожиданный ответ")
        if not response.ok or not data.get("ok"):
            detail = str(data.get("description") or f"HTTP {response.status_code}")
            raise TelegramApiError(detail.replace(self.token, "[redacted]"))
        return data.get("result")

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        result = self._request(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 25,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=35,
        )
        return result if isinstance(result, list) else []

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        html_mode: bool = False,
    ) -> int | None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if html_mode:
            payload["parse_mode"] = "HTML"
            payload["disable_web_page_preview"] = True
        result = self._request("sendMessage", payload)
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return message_id if isinstance(message_id, int) else None

    def edit_message(
        self, chat_id: int, message_id: int, text: str, *,
        reply_markup: dict[str, Any] | None = None, html_mode: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id, "message_id": message_id, "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if html_mode:
            payload["parse_mode"] = "HTML"
            payload["disable_web_page_preview"] = True
        self._request("editMessageText", payload)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._request("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def send_photo(self, chat_id: int, path: Path) -> None:
        try:
            with path.open("rb") as stream:
                response = self.session.post(
                    f"https://api.telegram.org/bot{self.token}/sendPhoto",
                    data={"chat_id": str(chat_id)},
                    files={"photo": (path.name, stream, "image/png")},
                    timeout=45,
                )
            data = response.json()
        except (requests.RequestException, OSError, ValueError) as exc:
            raise TelegramApiError("Не удалось отправить снимок формы в Telegram") from exc
        if not response.ok or not isinstance(data, dict) or not data.get("ok"):
            raise TelegramApiError("Telegram не принял снимок формы")


def _shorten(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1].rstrip()}…"


def _callback_ref(source: str, source_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z_]{2,20}", source)) and bool(
        re.fullmatch(r"[A-Za-z0-9_-]{1,32}", source_id)
    )


def vacancy_message(
    vacancy: Vacancy,
    profile: CandidateProfile | None,
    index: int,
    total: int,
    templates: tuple[ReplyTemplate, ...] = DEFAULT_TEMPLATES,
) -> str:
    title = html.escape(vacancy.title)
    company = html.escape(vacancy.company)
    area = html.escape(vacancy.area)
    summary = html.escape(_shorten(vacancy.summary, 320))
    url = html.escape(vacancy.url, quote=True)
    message = (
        f"<b>{index}/{total} · {title}</b>\n"
        f"{company} · {area}\n"
        f"{html.escape(vacancy.source)} · {'Заказ' if vacancy.kind == 'freelance' else 'Вакансия'} "
        f"· {html.escape(vacancy.market.upper())} · "
        f"{'Бюджет' if vacancy.kind == 'freelance' else 'Зарплата'}: "
        f"{html.escape(vacancy.pay_label or _salary(vacancy))}\n"
        f"География: {html.escape(vacancy.location_scope or vacancy.area)}\n"
        f"{summary or 'Краткое описание отсутствует.'}\n\n"
        f'<a href="{url}">Открыть вакансию ↗</a>\n'
        f"{url}"
    )
    if profile is not None:
        template = select_template(vacancy, templates)
        try:
            application = html.escape(render_reply(profile, vacancy, template))
            appendix = (
                f"\n\n<b>Шаблон: {html.escape(template.name)}</b>\n"
                f"<b>Готовый текст отклика:</b>\n<pre>{application}</pre>"
            )
            if len(message + appendix) > 4000:
                raise TemplateError("Готовый отклик слишком длинный для карточки вакансии")
            message += appendix
        except TemplateError as exc:
            message += f"\n\nТекст отклика недоступен: {html.escape(str(exc))}."
    return message


class JobTelegramBot:
    def __init__(
        self,
        config: AppConfig,
        api: TelegramApi,
        *,
        scanner: Callable[[AppConfig], ScanResult] = scan_vacancies,
        application_manager: ApplicationManager | None = None,
    ) -> None:
        self.config = config
        self.api = api
        self.scanner = scanner
        self.new_items: dict[int, list[Vacancy]] = {}
        self.history_items: dict[int, list[Vacancy]] = {}
        self.pending_edits: dict[int, tuple[str, str]] = {}
        self.profile_setup: set[int] = set()
        self.applications = application_manager or ApplicationManager(
            config, headless=os.environ.get("JOB_SEARCH_HEADLESS") == "1"
        )

    def _search_settings(self) -> SearchConfig:
        path = search_settings_path(self.config.database_path)
        return load_search_settings(self.config.search, path)

    def _save_search_settings(self, **changes: Any) -> None:
        updated = replace(self._search_settings(), **changes)
        save_search_settings(updated, search_settings_path(self.config.database_path))
        self.history_items.clear()

    def show_settings(self, chat_id: int) -> None:
        self.api.send_message(
            chat_id,
            "Что изменить? Настройки поиска применяются к следующему запуску. "
            "Профиль для отклика можно оставить пустым.",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "🔎 Фильтры поиска", "callback_data": "settings:search"}],
                    [{"text": "⏰ Ежедневный поиск", "callback_data": "settings:schedule"}],
                    [{"text": "📝 Данные для отклика", "callback_data": "settings:profile"}],
                    [{"text": "✉️ Шаблоны отклика", "callback_data": "templates"}],
                    [{"text": "← Назад", "callback_data": "menu:main"}],
                ]
            },
        )

    def show_search_settings(self, chat_id: int) -> None:
        try:
            settings = self._search_settings()
        except ConfigError as exc:
            self.api.send_message(chat_id, f"Не удалось прочитать фильтры: {exc}")
            return
        experience = ", ".join(EXPERIENCE_LABELS[name] for name in settings.experience_ids) or "любой"
        area = (
            "вся Россия"
            if settings.area_ids == ("113",)
            else ", ".join(settings.area_ids) or "без ограничения"
        )
        self.api.send_message(
            chat_id,
            "Фильтры поиска:\n"
            f"Режим: {'категории' if settings.categories else 'текстовые запросы'}\n"
            f"Профессии: {', '.join(CATEGORY_LABELS[name] for name in settings.categories) or 'любые'}\n"
            f"Роли HH: {', '.join(HH_ROLE_LABELS[name] for name in settings.role_ids) or 'все в категориях'}\n"
            f"Слова в названии: {', '.join(settings.title_keywords) or 'не заданы'}\n"
            f"Запросы: {', '.join(settings.queries) or 'нет'}"
            f"{' (сейчас не используются)' if settings.categories else ''}\n"
            f"Источники: {', '.join(settings.sources)}\n"
            f"Типы: {', '.join(settings.kinds)}\n"
            f"Удалённо: {'да' if settings.remote_only else 'нет'}\n"
            f"Только полностью удалённо: {'да' if settings.strict_remote else 'нет'}\n"
            f"Опыт: {experience}\n"
            f"Занятость HH: {', '.join(EMPLOYMENT_LABELS[name] for name in settings.employment_forms) or 'любая'}\n"
            f"График HH: {', '.join(SCHEDULE_LABELS[name] for name in settings.work_schedules) or 'любой'}\n"
            f"Зарплата: {'от ' + str(settings.salary_min) + ' ' + settings.salary_currency if settings.salary_min is not None else 'без минимума'}"
            f"; только с указанной: {'да' if settings.salary_required else 'нет'}\n"
            f"Регион: {area}\n"
            f"Опубликовано за: {settings.days} дн.\n"
            f"На каждый запрос: {settings.per_query}\n"
            f"Исключить слова: {', '.join(settings.excluded_keywords) or 'нет'}",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Профессиональные категории", "callback_data": "choose:categories"}],
                    [{"text": "Точные роли HH", "callback_data": "choose:roles"}],
                    [{"text": "Слова в названии", "callback_data": "edit:search:title_keywords"}],
                    [{"text": "Запросы (текстовый режим)", "callback_data": "edit:search:queries"}],
                    [
                        {"text": "Источники", "callback_data": "choose:sources"},
                        {"text": "Работа / заказы", "callback_data": "choose:kinds"},
                    ],
                    [
                        {
                            "text": "Удалённо ✓" if settings.remote_only else "Удалённо ○",
                            "callback_data": "toggle:remote",
                        },
                        {
                            "text": "Строго ✓" if settings.strict_remote else "Строго ○",
                            "callback_data": "toggle:strict",
                        },
                    ],
                    [
                        {"text": "Опыт", "callback_data": "choose:experience"},
                        {"text": "Регион", "callback_data": "choose:area"},
                    ],
                    [
                        {"text": "Занятость HH", "callback_data": "choose:employment"},
                        {"text": "График HH", "callback_data": "choose:work_schedule"},
                    ],
                    [{"text": "Зарплата", "callback_data": "choose:salary"}],
                    [
                        {"text": "Давность", "callback_data": "choose:days"},
                        {"text": "Лимит", "callback_data": "choose:limit"},
                    ],
                    [{"text": "Исключить слова", "callback_data": "edit:search:excluded_keywords"}],
                    [{"text": "← Настройки", "callback_data": "settings"}],
                ]
            },
        )

    def show_profile_settings(self, chat_id: int) -> None:
        try:
            initialize_profile(self.config.profile_path)
            fields = read_profile_fields(self.config.profile_path)
            missing = [PROFILE_LABELS[key] for key in missing_reply_fields(fields)]
            try:
                load_profile(self.config.profile_path)
                readiness = "Готовый текст отклика включён."
            except ProfileError:
                readiness = (
                    "Для текста отклика заполните: " + ", ".join(missing) + "."
                    if missing else "Проверьте данные профиля: текст отклика пока недоступен."
                )
        except ProfileError as exc:
            self.api.send_message(chat_id, f"Не удалось прочитать профиль: {exc}")
            return
        display = []
        for field, label in PROFILE_LABELS.items():
            value = fields.get(field)
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            display.append(f"{label}: {_shorten(str(value or 'не указано'), 100)}")
        facts = fields.get("facts", {})
        if isinstance(facts, dict):
            for key, value in list(facts.items())[:10]:
                display.append(f"{key}: {_shorten(str(value), 100)}")
            if len(facts) > 10:
                display.append(f"И ещё {len(facts) - 10} дополнительных фактов.")
        buttons = [[{"text": "📋 Заполнить основу", "callback_data": "profile:setup"}]]
        buttons += [
            [{"text": label, "callback_data": f"edit:profile:{field}"}]
            for field, label in PROFILE_LABELS.items()
        ]
        buttons.append([{"text": "➕ Дополнительный факт", "callback_data": "edit:fact:new"}])
        buttons.append([{"text": "← Настройки", "callback_data": "settings"}])
        self.api.send_message(
            chat_id,
            f"{readiness}\nФайл профиля хранится локально; сообщения для редактирования "
            "проходят через Telegram. В анкету подставляются лишь заполненные факты.\n"
            + "\n".join(display),
            reply_markup={"inline_keyboard": buttons},
        )

    def _reply_templates(self) -> tuple[ReplyTemplate, ...]:
        return load_templates(templates_path(self.config.database_path))

    def _next_profile_setup_field(self, chat_id: int) -> None:
        initialize_profile(self.config.profile_path)
        missing = missing_reply_fields(read_profile_fields(self.config.profile_path))
        if missing:
            self._begin_edit(chat_id, "profile", missing[0])
            return
        self.profile_setup.discard(chat_id)
        self.api.send_message(chat_id, "Основа профиля готова. Дополните её фактами для анкет.")
        self.show_profile_settings(chat_id)

    def show_templates(self, chat_id: int) -> None:
        try:
            templates = self._reply_templates()
        except TemplateError as exc:
            self.api.send_message(chat_id, f"Не удалось прочитать шаблоны: {exc}")
            return
        buttons = [
            [{"text": template.name, "callback_data": f"templates:{template.key}"}]
            for template in templates
        ]
        buttons.append([{"text": "← Настройки", "callback_data": "settings"}])
        self.api.send_message(
            chat_id,
            "Шаблон выбирается по словам в названии вакансии. Общий используется, "
            "если совпадений нет. Выберите шаблон, чтобы изменить текст или слова поиска.",
            reply_markup={"inline_keyboard": buttons},
        )

    def show_template(self, chat_id: int, key: str) -> None:
        try:
            template = next(item for item in self._reply_templates() if item.key == key)
        except (TemplateError, StopIteration) as exc:
            self.api.send_message(chat_id, f"Не удалось открыть шаблон: {exc}")
            return
        buttons = [[{"text": "Изменить текст", "callback_data": f"edit:template:{key}:body"}]]
        if key not in {"general", "freelance_ru", "freelance_global", "job_global"}:
            buttons.append(
                [{"text": "Слова для выбора", "callback_data": f"edit:template:{key}:keywords"}]
            )
        buttons.append(
            [{"text": "Поле анкеты", "callback_data": f"edit:template:{key}:form_value"}]
        )
        buttons.append([{"text": "← Шаблоны", "callback_data": "templates"}])
        self.api.send_message(
            chat_id,
            f"Шаблон: {template.name}\n"
            f"Слова: {', '.join(template.keywords) or 'используется по умолчанию'}\n\n"
            f"{template.body}\n\n"
            f"Поля анкеты: {', '.join(key for key, _ in template.form_values) or 'нет'}",
            reply_markup={"inline_keyboard": buttons},
        )

    def _schedule_settings(self):
        return load_schedule_settings(
            self.config.schedule, schedule_settings_path(self.config.database_path)
        )

    def show_schedule_settings(self, chat_id: int) -> None:
        try:
            schedule = self._schedule_settings()
        except ConfigError as exc:
            self.api.send_message(chat_id, f"Не удалось прочитать расписание: {exc}")
            return
        self.api.send_message(
            chat_id,
            f"Ежедневный поиск: {'включён' if schedule.enabled else 'выключен'}\n"
            f"Время: {schedule.time} ({schedule.timezone})\n"
            "Бот должен работать на включённом компьютере или сервере.",
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": "Выключить" if schedule.enabled else "Включить",
                            "callback_data": "toggle:schedule",
                        }
                    ],
                    [{"text": "Время", "callback_data": "edit:schedule:time"}],
                    [{"text": "Часовой пояс", "callback_data": "edit:schedule:timezone"}],
                    [{"text": "← Настройки", "callback_data": "settings"}],
                ]
            },
        )
    def _begin_edit(self, chat_id: int, kind: str, field: str) -> None:
        if kind == "template":
            key, _, template_field = field.partition(":")
            if key not in {item.key for item in DEFAULT_TEMPLATES} or template_field not in {
                "body",
                "keywords",
                "form_value",
            }:
                return
            if key in {"general", "freelance_ru", "freelance_global", "job_global"} and template_field == "keywords":
                return
            label = {
                "body": "Текст шаблона",
                "keywords": "Слова для выбора",
                "form_value": "Поле анкеты для этого шаблона",
            }[template_field]
            extra = (
                " Доступные поля: {name}, {title}, {company}, {about}, {skills_line}, "
                "{resume_line}, {portfolio_line}, {contact_line}, {about_en}, "
                "{experience_line}, {education_line}, {languages_line}, "
                "{availability_line}, {rate_line}."
                if template_field == "body"
                else (
                    " Перечислите через запятую; '-' очистит список."
                    if template_field == "keywords"
                    else " Формат: название поля = значение. Для удаления: название поля = -."
                )
            )
        elif kind == "schedule" and field in {"time", "timezone"}:
            label = "время HH:MM" if field == "time" else "часовой пояс IANA"
            extra = " Например, 09:00." if field == "time" else " Например, Asia/Novosibirsk."
        elif kind == "fact" and field == "new":
            label = "Дополнительный факт профиля"
            extra = " Формат: название поля = значение. Для удаления: название поля = -."
        elif kind == "profile" and field in EDITABLE_FIELDS:
            label = PROFILE_LABELS[field]
            extra = " Навыки перечислите через запятую." if field == "skills" else ""
            extra += (
                " Отправьте '-' для очистки."
                if field not in {"name", "about", "contact"}
                else ""
            )
        elif kind == "search" and field in {
            "queries", "excluded_keywords", "area_ids", "title_keywords", "salary_min"
        }:
            label = {
                "queries": "Поисковые запросы",
                "excluded_keywords": "Исключаемые слова",
                "area_ids": "ID регионов HeadHunter",
                "title_keywords": "Обязательные слова или фразы в названии (достаточно одного)",
                "salary_min": "Минимальная зарплата числом в выбранной валюте",
            }[field]
            extra = "" if field == "salary_min" else " Перечислите через запятую или с новой строки."
            if field != "queries":
                extra += " Отправьте '-' для очистки."
        else:
            return
        self.pending_edits[chat_id] = (kind, field)
        self.api.send_message(
            chat_id, f"Пришлите новое значение: {label}.{extra} Для отмены /cancel."
        )

    def _apply_edit(self, chat_id: int, text: str) -> None:
        kind, field = self.pending_edits[chat_id]
        try:
            if kind == "template":
                key, _, template_field = field.partition(":")
                save_template_field(
                    templates_path(self.config.database_path), key, template_field, text
                )
            elif kind == "profile":
                save_profile_field(self.config.profile_path, field, text)
            elif kind == "fact":
                name, separator, value = text.partition("=")
                if not separator:
                    raise ProfileError("Отправьте «Название поля = значение»")
                save_custom_fact(self.config.profile_path, name, value)
            elif kind == "schedule":
                current = self._schedule_settings()
                updated = validate_schedule(
                    current.enabled,
                    text.strip() if field == "time" else current.time,
                    text.strip() if field == "timezone" else current.timezone,
                )
                save_schedule_settings(
                    updated, schedule_settings_path(self.config.database_path)
                )
            else:
                if field == "salary_min":
                    value = None if text.strip() == "-" else int(text.strip())
                    self._save_search_settings(salary_min=value)
                    del self.pending_edits[chat_id]
                    self.api.send_message(chat_id, "Сохранено.")
                    self.show_search_settings(chat_id)
                    return
                items = tuple(item.strip() for item in re.split(r"[,;\n]", text) if item.strip())
                if text.strip() == "-":
                    items = ()
                if field == "queries" and not items:
                    raise ConfigError("Укажите хотя бы один поисковый запрос")
                if len(items) > (10 if field in {"queries", "title_keywords"} else 30) or any(
                    len(item) > 80 for item in items
                ):
                    raise ConfigError("Слишком много значений или слишком длинный текст")
                if field == "area_ids" and any(not item.isdigit() for item in items):
                    raise ConfigError("Для региона нужны числовые ID HeadHunter")
                self._save_search_settings(**{field: items})
        except (ConfigError, ProfileError, TemplateError, OSError, ValueError) as exc:
            self.api.send_message(chat_id, f"Не сохранил: {exc}. Попробуйте ещё раз или /cancel.")
            return
        del self.pending_edits[chat_id]
        self.api.send_message(chat_id, "Сохранено.")
        if kind == "profile" and chat_id in self.profile_setup:
            self._next_profile_setup_field(chat_id)
            return
        if kind == "template":
            self.show_template(chat_id, field.partition(":")[0])
        elif kind in {"profile", "fact"}:
            self.show_profile_settings(chat_id)
        elif kind == "schedule":
            self.show_schedule_settings(chat_id)
        else:
            self.show_search_settings(chat_id)

    def _show_choices(self, chat_id: int, choice: str) -> None:
        if choice == "sources":
            settings = self._search_settings()
            buttons = [
                [
                    {
                        "text": f"{'✓' if name in settings.sources else '○'} {label}",
                        "callback_data": f"toggle:source:{name}",
                    }
                ]
                for name, label in SOURCE_LABELS.items()
            ]
        elif choice == "kinds":
            settings = self._search_settings()
            buttons = [
                [
                    {
                        "text": f"{'✓' if kind in settings.kinds else '○'} {label}",
                        "callback_data": f"toggle:kind:{kind}",
                    }
                ]
                for kind, label in (("job", "Вакансии"), ("freelance", "Заказы"))
            ]
        elif choice == "categories":
            settings = self._search_settings()
            buttons = [
                [
                    {
                        "text": f"{'✓' if name in settings.categories else '○'} {label}",
                        "callback_data": f"toggle:category:{name}",
                    }
                ]
                for name, label in CATEGORY_LABELS.items()
            ]
            buttons.append(
                [{"text": "Поиск по запросам вместо категорий", "callback_data": "set:categories:any"}]
            )
        elif choice == "roles":
            settings = self._search_settings()
            buttons = [
                [{"text": f"{'✓' if role in settings.role_ids else '○'} {label}",
                  "callback_data": f"toggle:role:{role}"}]
                for role, label in HH_ROLE_LABELS.items()
            ]
            buttons.append([{"text": "Все роли категорий", "callback_data": "set:roles:any"}])
        elif choice == "employment":
            settings = self._search_settings()
            buttons = [
                [{"text": f"{'✓' if form in settings.employment_forms else '○'} {label}",
                  "callback_data": f"toggle:employment:{form}"}]
                for form, label in EMPLOYMENT_LABELS.items()
            ]
        elif choice == "work_schedule":
            settings = self._search_settings()
            buttons = [
                [{"text": f"{'✓' if schedule in settings.work_schedules else '○'} {label}",
                  "callback_data": f"toggle:work_schedule:{schedule}"}]
                for schedule, label in SCHEDULE_LABELS.items()
            ]
        elif choice == "salary":
            settings = self._search_settings()
            buttons = [
                [{"text": "Минимальная сумма", "callback_data": "edit:search:salary_min"}],
                [{"text": f"{'✓' if settings.salary_required else '○'} Только с зарплатой",
                  "callback_data": "toggle:salary_required"}],
                *[[{"text": f"{'✓' if settings.salary_currency == code else '○'} {code}",
                    "callback_data": f"set:currency:{code}"}] for code in ("RUR", "USD", "EUR")],
            ]
        elif choice == "experience":
            settings = self._search_settings()
            buttons = [
                [{"text": f"{'✓' if value in settings.experience_ids else '○'} {label}",
                  "callback_data": f"toggle:experience:{value}"}]
                for value, label in EXPERIENCE_LABELS.items()
            ]
            buttons.append([{"text": "Любой опыт", "callback_data": "set:experience:any"}])
        elif choice == "area":
            buttons = [
                [{"text": "Вся Россия", "callback_data": "set:area:ru"}],
                [{"text": "Без ограничения", "callback_data": "set:area:any"}],
                [{"text": "Ввести ID регионов", "callback_data": "edit:search:area_ids"}],
            ]
        elif choice == "days":
            buttons = [
                [{"text": f"{days} дн.", "callback_data": f"set:days:{days}"}]
                for days in (3, 7, 14, 30)
            ]
        elif choice == "limit":
            buttons = [
                [{"text": str(limit), "callback_data": f"set:limit:{limit}"}]
                for limit in (20, 50, 100)
            ]
        else:
            return
        buttons.append([{"text": "← Фильтры", "callback_data": "settings:search"}])
        self.api.send_message(
            chat_id, "Выберите значение:", reply_markup={"inline_keyboard": buttons}
        )

    def _apply_choice(self, chat_id: int, action: str) -> None:
        try:
            settings = self._search_settings()
            if action == "toggle:remote":
                enabled = not settings.remote_only
                self._save_search_settings(remote_only=enabled, strict_remote=enabled)
            elif action == "toggle:strict":
                enabled = not settings.strict_remote
                self._save_search_settings(strict_remote=enabled, remote_only=True)
            elif action == "toggle:schedule":
                current = self._schedule_settings()
                updated = validate_schedule(
                    not current.enabled, current.time, current.timezone
                )
                save_schedule_settings(updated, schedule_settings_path(self.config.database_path))
                self.show_schedule_settings(chat_id)
                return
            elif action.startswith("toggle:source:"):
                source = action.removeprefix("toggle:source:")
                if source not in SOURCE_LABELS:
                    return
                selected = set(settings.sources)
                if source in selected:
                    selected.remove(source)
                else:
                    selected.add(source)
                if not selected:
                    raise ConfigError("Оставьте хотя бы один источник")
                self._save_search_settings(
                    sources=tuple(name for name in SOURCE_LABELS if name in selected)
                )
                self._show_choices(chat_id, "sources")
                return
            elif action.startswith("toggle:kind:"):
                kind = action.removeprefix("toggle:kind:")
                if kind not in {"job", "freelance"}:
                    return
                selected = set(settings.kinds)
                if kind in selected:
                    selected.remove(kind)
                else:
                    selected.add(kind)
                if not selected:
                    raise ConfigError("Оставьте хотя бы один тип")
                self._save_search_settings(
                    kinds=tuple(name for name in ("job", "freelance") if name in selected)
                )
                self._show_choices(chat_id, "kinds")
                return
            elif action.startswith("toggle:category:"):
                category = action.removeprefix("toggle:category:")
                if category not in CATEGORY_LABELS:
                    return
                selected = set(settings.categories)
                if category in selected:
                    selected.remove(category)
                else:
                    selected.add(category)
                self._save_search_settings(
                    categories=tuple(name for name in CATEGORY_LABELS if name in selected)
                )
                self._show_choices(chat_id, "categories")
                return
            elif action.startswith("toggle:role:"):
                role = action.removeprefix("toggle:role:")
                if role not in HH_ROLE_LABELS:
                    return
                selected = set(settings.role_ids)
                selected.symmetric_difference_update({role})
                self._save_search_settings(
                    role_ids=tuple(name for name in HH_ROLE_LABELS if name in selected)
                )
                self._show_choices(chat_id, "roles")
                return
            elif action == "set:roles:any":
                self._save_search_settings(role_ids=())
                self._show_choices(chat_id, "roles")
                return
            elif action.startswith("toggle:employment:"):
                form = action.removeprefix("toggle:employment:")
                if form not in EMPLOYMENT_LABELS:
                    return
                selected = set(settings.employment_forms)
                selected.symmetric_difference_update({form})
                self._save_search_settings(
                    employment_forms=tuple(name for name in EMPLOYMENT_LABELS if name in selected)
                )
                self._show_choices(chat_id, "employment")
                return
            elif action.startswith("toggle:work_schedule:"):
                schedule = action.removeprefix("toggle:work_schedule:")
                if schedule not in SCHEDULE_LABELS:
                    return
                selected = set(settings.work_schedules)
                selected.symmetric_difference_update({schedule})
                self._save_search_settings(
                    work_schedules=tuple(name for name in SCHEDULE_LABELS if name in selected)
                )
                self._show_choices(chat_id, "work_schedule")
                return
            elif action.startswith("toggle:experience:"):
                experience = action.removeprefix("toggle:experience:")
                if experience not in EXPERIENCE_LABELS:
                    return
                selected = set(settings.experience_ids)
                selected.symmetric_difference_update({experience})
                self._save_search_settings(
                    experience_ids=tuple(name for name in EXPERIENCE_LABELS if name in selected)
                )
                self._show_choices(chat_id, "experience")
                return
            elif action == "toggle:salary_required":
                self._save_search_settings(salary_required=not settings.salary_required)
                self._show_choices(chat_id, "salary")
                return
            elif action.startswith("set:currency:"):
                code = action.removeprefix("set:currency:")
                if code not in {"RUR", "USD", "EUR"}:
                    return
                self._save_search_settings(salary_currency=code)
                self._show_choices(chat_id, "salary")
                return
            elif action == "set:categories:any":
                self._save_search_settings(categories=())
                self._show_choices(chat_id, "categories")
                return
            elif action == "set:experience:any":
                self._save_search_settings(experience_ids=())
            elif action in {
                "set:experience:entry", "set:experience:one_three", "set:experience:junior"
            }:
                previous = {
                    "set:experience:entry": ("noExperience",),
                    "set:experience:one_three": ("between1And3",),
                    "set:experience:junior": ("noExperience", "between1And3"),
                }
                self._save_search_settings(experience_ids=previous[action])
            elif action == "set:area:ru":
                self._save_search_settings(area_ids=("113",))
            elif action == "set:area:any":
                self._save_search_settings(area_ids=())
            elif action.startswith("set:days:") and action.removeprefix("set:days:") in {
                "3",
                "7",
                "14",
                "30",
            }:
                self._save_search_settings(days=int(action.removeprefix("set:days:")))
            elif action.startswith("set:limit:") and action.removeprefix("set:limit:") in {
                "20",
                "50",
                "100",
            }:
                self._save_search_settings(per_query=int(action.removeprefix("set:limit:")))
            else:
                return
        except (ConfigError, OSError) as exc:
            self.api.send_message(chat_id, f"Не сохранил фильтр: {exc}")
            return
        self.show_search_settings(chat_id)

    def _allowed(self, user_id: int, chat_id: int, chat_type: str) -> bool:
        if chat_type != "private" or chat_id != user_id:
            return False
        if user_id not in self.config.telegram.allowed_user_ids:
            self.api.send_message(
                chat_id,
                f"Доступ закрыт. Ваш Telegram ID: {user_id}. "
                "Добавьте его в telegram.allowed_user_ids локального config.toml.",
            )
            return False
        return True

    def _optional_profile(self, chat_id: int) -> CandidateProfile | None:
        if not self.config.profile_path.is_file():
            return None
        try:
            return load_profile(self.config.profile_path)
        except ProfileError as exc:
            self.api.send_message(
                chat_id,
                f"Текст отклика пока недоступен: {exc}. Вакансии и ссылки покажу без него.",
            )
            return None

    def _optional_templates(self, chat_id: int) -> tuple[ReplyTemplate, ...]:
        try:
            return self._reply_templates()
        except TemplateError as exc:
            self.api.send_message(
                chat_id, f"Не удалось прочитать шаблоны: {exc}. Использую стандартные."
            )
            return DEFAULT_TEMPLATES

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        callback = update.get("callback_query")
        if isinstance(message, dict):
            chat = message.get("chat") or {}
            user = message.get("from") or {}
            chat_id = chat.get("id")
            user_id = user.get("id")
            if not isinstance(chat_id, int) or not isinstance(user_id, int):
                return
            if chat.get("type") != "private":
                return
            text = str(message.get("text") or "")
            if text.startswith("/id"):
                self.api.send_message(chat_id, f"Ваш Telegram ID: {user_id}")
                return
            if not self._allowed(user_id, chat_id, "private"):
                return
            if text == "/cancel":
                self.pending_edits.pop(chat_id, None)
                self.profile_setup.discard(chat_id)
                self.api.send_message(
                    chat_id, "Редактирование отменено.", reply_markup=MAIN_KEYBOARD
                )
            elif text.startswith("/start"):
                self.pending_edits.pop(chat_id, None)
                self.profile_setup.discard(chat_id)
                self.api.send_message(
                    chat_id,
                    "Нажмите «Искать вакансии», чтобы проверить новые подходящие позиции. "
                    "В настройках можно изменить фильтры и данные для отклика.",
                    reply_markup=MAIN_KEYBOARD,
                )
            elif text == SEARCH_BUTTON or text.startswith("/scan"):
                self.pending_edits.pop(chat_id, None)
                self.scan(chat_id)
            elif text == HISTORY_BUTTON or text.startswith("/history"):
                self.pending_edits.pop(chat_id, None)
                self.show_history(chat_id, 0)
            elif text == SETTINGS_BUTTON or text.startswith("/settings"):
                self.pending_edits.pop(chat_id, None)
                self.profile_setup.discard(chat_id)
                self.show_settings(chat_id)
            elif text.startswith("/profile"):
                self.pending_edits.pop(chat_id, None)
                self.show_profile_settings(chat_id)
            elif (
                chat_id in self.pending_edits
                and self.pending_edits[chat_id][0] == "application_url"
                and not text.startswith("/")
            ):
                self._prepare_application(chat_id, text)
            elif chat_id in self.pending_edits and not text.startswith("/"):
                self._apply_edit(chat_id, text)
            else:
                self.api.send_message(
                    chat_id,
                    "Используйте кнопки поиска, истории и настроек.",
                    reply_markup=MAIN_KEYBOARD,
                )
            return

        if isinstance(callback, dict):
            callback_id = str(callback.get("id") or "")
            chat = (callback.get("message") or {}).get("chat") or {}
            user_id = (callback.get("from") or {}).get("id")
            chat_id = chat.get("id")
            if not isinstance(chat_id, int) or not isinstance(user_id, int):
                return
            if (
                chat.get("type") != "private"
                or chat_id != user_id
                or user_id not in self.config.telegram.allowed_user_ids
            ):
                self.api.answer_callback(callback_id, "Нет доступа")
                return
            self.api.answer_callback(callback_id)
            action = str(callback.get("data") or "")
            message_id = (callback.get("message") or {}).get("message_id")
            if not isinstance(message_id, int):
                message_id = None
            if action == "scan":
                self.pending_edits.pop(chat_id, None)
                self.scan(chat_id)
            elif action.startswith("new:") and action[4:].isdigit() and len(action) <= 10:
                self.show_new(chat_id, int(action[4:]), message_id=message_id)
            elif action.startswith("history:") and action[8:].isdigit() and len(action) <= 14:
                self.show_history(chat_id, int(action[8:]), message_id=message_id)
            elif action == "menu:main":
                self.pending_edits.pop(chat_id, None)
                self.profile_setup.discard(chat_id)
                self.api.send_message(
                    chat_id, "Главное меню: выберите действие.", reply_markup=MAIN_KEYBOARD
                )
            elif action == "settings":
                self.pending_edits.pop(chat_id, None)
                self.profile_setup.discard(chat_id)
                self.show_settings(chat_id)
            elif action == "settings:search":
                self.pending_edits.pop(chat_id, None)
                self.show_search_settings(chat_id)
            elif action == "settings:schedule":
                self.pending_edits.pop(chat_id, None)
                self.show_schedule_settings(chat_id)
            elif action == "settings:profile":
                self.pending_edits.pop(chat_id, None)
                self.show_profile_settings(chat_id)
            elif action == "profile:setup":
                self.profile_setup.add(chat_id)
                self._next_profile_setup_field(chat_id)
            elif action == "templates":
                self.pending_edits.pop(chat_id, None)
                self.show_templates(chat_id)
            elif action.startswith("templates:"):
                self.pending_edits.pop(chat_id, None)
                self.show_template(chat_id, action.removeprefix("templates:"))
            elif action.startswith("reply:"):
                parts = action.split(":")
                if len(parts) == 3 and _callback_ref(parts[1], parts[2]):
                    self.show_reply_choices(chat_id, parts[1], parts[2])
            elif action.startswith("replypick:"):
                parts = action.split(":")
                if len(parts) == 4 and _callback_ref(parts[1], parts[2]):
                    self.show_reply_with_template(chat_id, parts[1], parts[2], parts[3])
            elif action.startswith("appprep:"):
                parts = action.split(":")
                if len(parts) == 3 and _callback_ref(parts[1], parts[2]):
                    self._begin_application(chat_id, parts[1], parts[2])
            elif action.startswith("appfind:"):
                parts = action.split(":")
                if len(parts) == 3 and _callback_ref(parts[1], parts[2]):
                    self._discover_application(chat_id, parts[1], parts[2])
            elif action.startswith("apprefresh:"):
                parts = action.split(":")
                if len(parts) == 3 and _callback_ref(parts[1], parts[2]):
                    try:
                        self.show_application_review(
                            chat_id, self.applications.refresh(parts[1], parts[2])
                        )
                    except (ApplicationStateError, FormProbeError, PlaywrightError, OSError) as exc:
                        self.api.send_message(chat_id, f"Не удалось проверить форму: {exc}")
            elif action.startswith("appclose:"):
                parts = action.split(":")
                if len(parts) == 3 and _callback_ref(parts[1], parts[2]):
                    self.applications.close(parts[1], parts[2])
                    with VacancyStore(self.config.database_path) as store:
                        record = store.application(parts[1], parts[2])
                    if record and record.status == "submitted":
                        result = "Заявка отправлена."
                    elif record and record.status == "attempted":
                        result = "Результат отправки неясен; не повторяйте её без проверки."
                    else:
                        result = "Отправки не было."
                    self.api.send_message(chat_id, f"Окно заявки закрыто. {result}")
            elif action.startswith("appsubmit:"):
                parts = action.split(":")
                if len(parts) == 4 and _callback_ref(parts[1], parts[2]):
                    self._submit_application(chat_id, parts[1], parts[2], parts[3])
            elif action.startswith("edit:"):
                parts = action.split(":", 2)
                if len(parts) == 3:
                    self._begin_edit(chat_id, parts[1], parts[2])
            elif action.startswith("choose:"):
                self._show_choices(chat_id, action.removeprefix("choose:"))
            elif action.startswith(("toggle:", "set:")):
                self._apply_choice(chat_id, action)

    def scan(self, chat_id: int, result: ScanResult | None = None) -> ScanResult | None:
        progress_id = None
        if result is None:
            progress_id = self.api.send_message(chat_id, "Ищу вакансии по сохранённым фильтрам…")
            try:
                current_config = replace(self.config, search=self._search_settings())
                result = self.scanner(current_config)
            except (ConfigError, HhApiError, SearchError, PlaywrightError, OSError) as exc:
                self._show_card(
                    chat_id, f"Поиск не удался: {_shorten(str(exc), 300)}",
                    message_id=progress_id,
                )
                return None
        self.new_items[chat_id] = result.new_items
        self.history_items.pop(chat_id, None)
        if result.errors:
            self.api.send_message(chat_id, "Часть источников недоступна: " + "; ".join(result.errors))
        if not result.new_items:
            self._show_card(
                chat_id,
                f"Новых подходящих вакансий нет. Проверено: {result.fetched_count}, "
                f"подошло: {result.accepted_count}. Можно посмотреть уже найденные.",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "📚 Посмотреть найденные", "callback_data": "history:0"}]
                    ]
                },
                message_id=progress_id,
            )
            return result
        self.show_new(chat_id, 0, message_id=progress_id)
        return result

    def _begin_application(self, chat_id: int, source: str, source_id: str) -> None:
        if self._optional_profile(chat_id) is None:
            self.api.send_message(chat_id, "Сначала заполните данные для отклика в настройках.")
            return
        with VacancyStore(self.config.database_path) as store:
            if store.get_vacancy(source, source_id) is None:
                self.api.send_message(chat_id, "Вакансия не найдена в истории.")
                return
            record = store.application(source, source_id)
        if record and record.status in {"attempted", "submitted"}:
            self.api.send_message(chat_id, "Заявка уже отправлена или результат попытки неясен.")
            return
        self.pending_edits[chat_id] = ("application_url", f"{source}:{source_id}")
        self.api.send_message(
            chat_id,
            "Можно попробовать найти анкету по ссылке вакансии либо прислать "
            "публичную HTTPS-ссылку на форму. "
            "Если форм несколько, добавьте через пробел её номер, начиная с 0. "
            "Для отмены /cancel.",
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": "🔎 Найти форму",
                            "callback_data": f"appfind:{source}:{source_id}",
                        }
                    ]
                ]
            },
        )

    def _discover_application(self, chat_id: int, source: str, source_id: str) -> None:
        with VacancyStore(self.config.database_path) as store:
            vacancy = store.get_vacancy(source, source_id)
        if vacancy is None:
            self.api.send_message(chat_id, "Предложение не найдено в истории.")
            return
        try:
            links = discover_application_links(vacancy.url)
        except (FormProbeError, PlaywrightError, OSError) as exc:
            self.api.send_message(chat_id, f"Не удалось проверить ссылку: {_shorten(str(exc), 300)}")
            return
        if not links:
            self.api.send_message(chat_id, "Явную форму не нашёл. Пришлите её ссылку вручную.")
        elif len(links) == 1:
            self.pending_edits[chat_id] = ("application_url", f"{source}:{source_id}")
            self._prepare_application(chat_id, links[0])
        else:
            self.api.send_message(
                chat_id,
                "Найдено несколько возможных ссылок. Откройте нужную и пришлите её мне:\n"
                + "\n".join(links),
            )

    def _prepare_application(self, chat_id: int, text: str) -> None:
        _, key = self.pending_edits[chat_id]
        source, source_id = key.split(":", 1)
        parts = text.strip().rsplit(" ", 1)
        url = parts[0] if len(parts) == 2 and parts[1].isdigit() else text.strip()
        form_index = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None
        self.api.send_message(chat_id, "Открываю форму и подготавливаю заявку…")
        try:
            summary = self.applications.prepare(source, source_id, url, form_index=form_index)
        except (
            ApplicationStateError,
            FormProbeError,
            FillError,
            ProfileError,
            TemplateError,
            PlaywrightError,
            OSError,
        ) as exc:
            self.api.send_message(
                chat_id, f"Не удалось подготовить заявку: {_shorten(str(exc), 300)}"
            )
            return
        self.pending_edits.pop(chat_id, None)
        self.show_application_review(chat_id, summary)

    def show_application_review(self, chat_id: int, summary: ReviewSummary) -> None:
        if getattr(self.applications, "headless", False):
            try:
                self.api.send_photo(chat_id, summary.screenshot_path)
            except (TelegramApiError, OSError) as exc:
                self.api.send_message(
                    chat_id,
                    f"Не удалось показать заполненную форму: {exc}. Отправка заблокирована.",
                )
                return
        missing = ", ".join(summary.missing_required) or "нет"
        uploaded = summary.uploaded_filename or "нет"
        buttons = [
            [
                {
                    "text": "🔄 Проверить снова",
                    "callback_data": f"apprefresh:{summary.source}:{summary.source_id}",
                }
            ]
        ]
        if not summary.missing_required:
            buttons.append(
                [
                    {
                        "text": "✅ Отправить эту заявку",
                        "callback_data": (
                            f"appsubmit:{summary.source}:{summary.source_id}:{summary.review_id}"
                        ),
                    }
                ]
            )
        buttons.append(
            [
                {
                    "text": "✖ Закрыть форму",
                    "callback_data": f"appclose:{summary.source}:{summary.source_id}",
                }
            ]
        )
        self.api.send_message(
            chat_id,
            f"Заявка подготовлена: {summary.title}\n"
            f"Форма: {summary.form_url}\n"
            f"Заполнены поля: {', '.join(summary.filled) or 'нет'}\n"
            f"Источник данных: {', '.join(summary.field_sources) or 'нет'}\n"
            f"Резюме прикреплено: {uploaded}\n"
            f"Обязательные поля для проверки: {missing}\n"
            + (
                "Проверьте снимок формы в Telegram. "
                if getattr(self.applications, "headless", False)
                else "Проверьте открытую форму в браузере. "
            )
            + "Снимок и отчёт сохранены локально. "
            "Заявка ещё не отправлена.",
            reply_markup={"inline_keyboard": buttons},
        )

    def _submit_application(
        self, chat_id: int, source: str, source_id: str, review_id: str
    ) -> None:
        try:
            outcome = self.applications.submit(source, source_id, review_id)
        except (ApplicationStateError, FormProbeError, PlaywrightError, OSError) as exc:
            with VacancyStore(self.config.database_path) as store:
                record = store.application(source, source_id)
            if record and record.status == "attempted":
                self.api.send_message(
                    chat_id,
                    "Результат отправки неясен. Повторная отправка заблокирована; "
                    "проверьте страницу и ответ работодателя вручную.",
                )
            else:
                self.api.send_message(chat_id, f"Не отправил: {_shorten(str(exc), 300)}")
            return
        if outcome.status == "submitted":
            self.api.send_message(chat_id, "Сайт подтвердил отправку заявки. Статус сохранён.")
        else:
            self.api.send_message(
                chat_id,
                "Результат отправки неясен. Повторная отправка заблокирована; "
                "проверьте страницу и ответ работодателя вручную.",
            )

    def show_reply_choices(self, chat_id: int, source: str, source_id: str) -> None:
        if self._optional_profile(chat_id) is None:
            self.api.send_message(chat_id, "Заполните данные для отклика в настройках.")
            return
        with VacancyStore(self.config.database_path) as store:
            vacancy = store.get_vacancy(source, source_id)
        if vacancy is None:
            self.api.send_message(chat_id, "Вакансия не найдена в истории.")
            return
        templates = self._optional_templates(chat_id)
        selected = select_template(vacancy, templates)
        buttons = [
            [
                {
                    "text": f"{'✓ ' if item.key == selected.key else ''}{item.name}",
                    "callback_data": f"replypick:{source}:{source_id}:{item.key}",
                }
            ]
            for item in templates
        ]
        self.api.send_message(
            chat_id,
            f"Шаблон для вакансии «{vacancy.title}». Сейчас выбран: {selected.name}.",
            reply_markup={"inline_keyboard": buttons},
        )

    def show_reply_with_template(
        self, chat_id: int, source: str, source_id: str, template_key: str
    ) -> None:
        with VacancyStore(self.config.database_path) as store:
            vacancy = store.get_vacancy(source, source_id)
        if vacancy is None:
            self.api.send_message(chat_id, "Вакансия не найдена в истории.")
            return
        profile = self._optional_profile(chat_id)
        if profile is None:
            self.api.send_message(chat_id, "Заполните данные для отклика в настройках.")
            return
        templates = self._optional_templates(chat_id)
        template = next((item for item in templates if item.key == template_key), None)
        if template is None:
            self.api.send_message(chat_id, "Такой шаблон не найден.")
            return
        try:
            application = html.escape(render_reply(profile, vacancy, template))
        except TemplateError as exc:
            self.api.send_message(chat_id, f"Не удалось заполнить шаблон: {exc}")
            return
        title = html.escape(vacancy.title)
        url = html.escape(vacancy.url, quote=True)
        message = (
            f"<b>{title}</b> · шаблон «{html.escape(template.name)}»\n"
            f'<a href="{url}">Открыть вакансию ↗</a>\n{url}\n\n'
            f"<pre>{application}</pre>"
        )
        if len(message) > 4000:
            self.api.send_message(chat_id, "Отклик слишком длинный; сократите шаблон или профиль.")
            return
        self.api.send_message(chat_id, message, html_mode=True)

    def _reply_button(
        self, vacancy: Vacancy, profile: CandidateProfile | None
    ) -> dict[str, Any] | None:
        if (
            profile is None
            or not _callback_ref(vacancy.source, vacancy.source_id)
        ):
            return None
        buttons = [
            [
                {
                    "text": "📝 Другой шаблон",
                    "callback_data": f"reply:{vacancy.source}:{vacancy.source_id}",
                }
            ]
        ]
        with VacancyStore(self.config.database_path) as store:
            record = store.application(vacancy.source, vacancy.source_id)
        if record is None or record.status not in {"attempted", "submitted"}:
            buttons.append(
                [
                    {
                        "text": "📄 Подготовить заявку",
                        "callback_data": f"appprep:{vacancy.source}:{vacancy.source_id}",
                    }
                ]
            )
        return {"inline_keyboard": buttons}

    def _show_card(
        self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None,
        html_mode: bool = False, message_id: int | None = None,
    ) -> None:
        if message_id is None:
            self.api.send_message(chat_id, text, reply_markup=reply_markup, html_mode=html_mode)
        else:
            self.api.edit_message(
                chat_id, message_id, text, reply_markup=reply_markup, html_mode=html_mode
            )

    def show_new(self, chat_id: int, page: int, *, message_id: int | None = None) -> None:
        items = self.new_items.get(chat_id, [])
        if not items:
            self._show_card(
                chat_id, "Список новых вакансий пуст. Запустите поиск ещё раз.",
                message_id=message_id,
            )
            return
        if page >= len(items):
            self._show_card(chat_id, "Это последняя карточка новых вакансий.", message_id=message_id)
            return
        profile = self._optional_profile(chat_id)
        templates = self._optional_templates(chat_id) if profile is not None else DEFAULT_TEMPLATES
        vacancy = items[page]
        markup = self._reply_button(vacancy, profile) or {"inline_keyboard": []}
        arrows = []
        if page > 0:
            arrows.append({"text": "←", "callback_data": f"new:{page - 1}"})
        if page + 1 < len(items):
            arrows.append({"text": "→", "callback_data": f"new:{page + 1}"})
        if arrows:
            markup["inline_keyboard"].append(arrows)
        markup["inline_keyboard"].append(
            [{"text": "📚 Ранее найденные", "callback_data": "history:0"}]
        )
        self._show_card(
            chat_id, vacancy_message(vacancy, profile, page + 1, len(items), templates),
            reply_markup=markup, html_mode=True, message_id=message_id,
        )

    def show_history(self, chat_id: int, page: int, *, message_id: int | None = None) -> None:
        if page == 0 or chat_id not in self.history_items:
            try:
                settings = self._search_settings()
            except ConfigError as exc:
                self._show_card(chat_id, f"Не удалось прочитать фильтры: {exc}", message_id=message_id)
                return
            with VacancyStore(self.config.database_path) as store:
                all_items = store.recent_vacancies(store.count())
            self.history_items[chat_id] = [
                item for item in all_items if rejection_reason(item, settings) is None
            ]
        matches = self.history_items[chat_id]
        total = len(matches)
        if page >= total:
            self._show_card(
                chat_id,
                "Под текущие фильтры история пока пуста." if total == 0
                else "Это последняя карточка истории.",
                message_id=message_id,
            )
            return
        profile = self._optional_profile(chat_id)
        templates = self._optional_templates(chat_id) if profile is not None else DEFAULT_TEMPLATES
        vacancy = matches[page]
        markup = self._reply_button(vacancy, profile) or {"inline_keyboard": []}
        arrows = []
        if page > 0:
            arrows.append({"text": "←", "callback_data": f"history:{page - 1}"})
        if page + 1 < total:
            arrows.append({"text": "→", "callback_data": f"history:{page + 1}"})
        if arrows:
            markup["inline_keyboard"].append(arrows)
        if self.new_items.get(chat_id):
            markup["inline_keyboard"].append(
                [{"text": "✨ Новые", "callback_data": "new:0"}]
            )
        markup["inline_keyboard"].append(
            [{"text": "🔎 Искать новые", "callback_data": "scan"}]
        )
        self._show_card(
            chat_id, vacancy_message(vacancy, profile, page + 1, total, templates),
            reply_markup=markup, html_mode=True, message_id=message_id,
        )


def _load_offset(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return int(value["offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _save_offset(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    os.replace(temporary, path)


def run_bot(config: AppConfig) -> None:
    if not config.telegram.bot_token:
        raise ConfigError("Укажите telegram.bot_token в config.toml или TELEGRAM_BOT_TOKEN")
    api = TelegramApi(config.telegram.bot_token)
    bot = JobTelegramBot(config, api)
    offset_path = config.database_path.parent / "telegram-offset.json"
    scheduler = DailyScheduler(config.schedule, config.database_path.parent / "schedule-state.json")
    offset = _load_offset(offset_path)
    print("Telegram-бот запущен. Остановить: Ctrl+C.", flush=True)
    try:
        while True:
            try:
                scheduler.schedule = load_schedule_settings(
                    config.schedule, schedule_settings_path(config.database_path)
                )
            except ConfigError as exc:
                print(f"Расписание недоступно: {exc}", flush=True)
            due_day = scheduler.due()
            if due_day:
                scheduler.mark(due_day)
                if config.telegram.allowed_user_ids:
                    owner, *others = config.telegram.allowed_user_ids
                    try:
                        result = bot.scan(owner)
                        if result is not None:
                            for user_id in others:
                                bot.scan(user_id, result=result)
                    except (TelegramApiError, OSError, ValueError) as exc:
                        print(f"Ежедневный поиск не удался: {exc}", flush=True)
            try:
                updates = api.get_updates(offset)
            except TelegramApiError as exc:
                if "Conflict" in str(exc):
                    raise TelegramApiError("Другой экземпляр бота уже запущен") from exc
                if "Unauthorized" in str(exc):
                    raise TelegramApiError(
                        "Telegram отклонил токен бота. Проверьте bot_token"
                    ) from exc
                print(f"Telegram недоступен: {exc}. Повтор через 5 секунд.", flush=True)
                time.sleep(5)
                continue
            for update in updates:
                try:
                    bot.handle_update(update)
                except (TelegramApiError, OSError, ValueError) as exc:
                    print(f"Не удалось обработать команду: {exc}", flush=True)
                offset = int(update["update_id"]) + 1
                _save_offset(offset_path, offset)
    finally:
        bot.applications.close_all()
