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
from job_search_automation.config import (
    AppConfig,
    ConfigError,
    SearchConfig,
    load_search_settings,
    save_search_settings,
    search_settings_path,
)
from job_search_automation.forms import FormProbeError
from job_search_automation.hh import HhApiError
from job_search_automation.models import Vacancy
from job_search_automation.profile import (
    EDITABLE_FIELDS,
    CandidateProfile,
    ProfileError,
    load_profile,
    read_profile_fields,
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
}

SOURCE_LABELS = {
    "hh": "HeadHunter",
    "superjob": "SuperJob (нужен ключ)",
    "remotive": "Remotive",
    "wwr": "We Work Remotely",
    "fl": "FL.ru RSS",
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
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if html_mode:
            payload["parse_mode"] = "HTML"
            payload["disable_web_page_preview"] = True
        self._request("sendMessage", payload)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._request("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


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
        self.pending_edits: dict[int, tuple[str, str]] = {}
        self.applications = application_manager or ApplicationManager(config)

    def _search_settings(self) -> SearchConfig:
        path = search_settings_path(self.config.database_path)
        return load_search_settings(self.config.search, path)

    def _save_search_settings(self, **changes: Any) -> None:
        updated = replace(self._search_settings(), **changes)
        save_search_settings(updated, search_settings_path(self.config.database_path))

    def show_settings(self, chat_id: int) -> None:
        self.api.send_message(
            chat_id,
            "Что изменить? Настройки поиска применяются к следующему запуску. "
            "Профиль для отклика можно оставить пустым.",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "🔎 Фильтры поиска", "callback_data": "settings:search"}],
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
        experience = {
            (): "любой",
            ("noExperience",): "без опыта",
            ("between1And3",): "1–3 года",
            ("noExperience", "between1And3"): "без опыта и 1–3 года",
        }.get(settings.experience_ids, ", ".join(settings.experience_ids))
        area = (
            "вся Россия"
            if settings.area_ids == ("113",)
            else ", ".join(settings.area_ids) or "без ограничения"
        )
        self.api.send_message(
            chat_id,
            "Фильтры поиска:\n"
            f"Запросы: {', '.join(settings.queries)}\n"
            f"Источники: {', '.join(settings.sources)}\n"
            f"Типы: {', '.join(settings.kinds)}\n"
            f"Удалённо: {'да' if settings.remote_only else 'нет'}\n"
            f"Только полностью удалённо: {'да' if settings.strict_remote else 'нет'}\n"
            f"Опыт: {experience}\n"
            f"Регион: {area}\n"
            f"Опубликовано за: {settings.days} дн.\n"
            f"На каждый запрос: {settings.per_query}\n"
            f"Исключить слова: {', '.join(settings.excluded_keywords) or 'нет'}",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Запросы", "callback_data": "edit:search:queries"}],
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
            fields = read_profile_fields(self.config.profile_path)
            try:
                load_profile(self.config.profile_path)
                readiness = "Готовый текст отклика включён."
            except ProfileError:
                readiness = "Текст отклика пока не готов; поиск работает без него."
        except ProfileError as exc:
            self.api.send_message(chat_id, f"Не удалось прочитать профиль: {exc}")
            return
        display = []
        for field, label in PROFILE_LABELS.items():
            value = fields.get(field)
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            display.append(f"{label}: {_shorten(str(value or 'не указано'), 100)}")
        buttons = [
            [{"text": label, "callback_data": f"edit:profile:{field}"}]
            for field, label in PROFILE_LABELS.items()
        ]
        buttons.append([{"text": "← Настройки", "callback_data": "settings"}])
        self.api.send_message(
            chat_id,
            f"{readiness}\n" + "\n".join(display),
            reply_markup={"inline_keyboard": buttons},
        )

    def _reply_templates(self) -> tuple[ReplyTemplate, ...]:
        return load_templates(templates_path(self.config.database_path))

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
        if key != "general":
            buttons.append(
                [{"text": "Слова для выбора", "callback_data": f"edit:template:{key}:keywords"}]
            )
        buttons.append([{"text": "← Шаблоны", "callback_data": "templates"}])
        self.api.send_message(
            chat_id,
            f"Шаблон: {template.name}\n"
            f"Слова: {', '.join(template.keywords) or 'используется по умолчанию'}\n\n"
            f"{template.body}",
            reply_markup={"inline_keyboard": buttons},
        )

    def _begin_edit(self, chat_id: int, kind: str, field: str) -> None:
        if kind == "template":
            key, _, template_field = field.partition(":")
            if key not in {item.key for item in DEFAULT_TEMPLATES} or template_field not in {
                "body",
                "keywords",
            }:
                return
            if key == "general" and template_field == "keywords":
                return
            label = "Текст шаблона" if template_field == "body" else "Слова для выбора"
            extra = (
                " Доступные поля: {name}, {title}, {company}, {about}, {skills_line}, "
                "{resume_line}, {portfolio_line}, {contact_line}."
                if template_field == "body"
                else " Перечислите через запятую; '-' очистит список."
            )
        elif kind == "profile" and field in EDITABLE_FIELDS:
            label = PROFILE_LABELS[field]
            extra = " Навыки перечислите через запятую." if field == "skills" else ""
            extra += (
                " Отправьте '-' для очистки."
                if field in {"skills", "resume_url", "portfolio_url", "email", "phone", "city"}
                else ""
            )
        elif kind == "search" and field in {"queries", "excluded_keywords", "area_ids"}:
            label = {
                "queries": "Поисковые запросы",
                "excluded_keywords": "Исключаемые слова",
                "area_ids": "ID регионов HeadHunter",
            }[field]
            extra = " Перечислите через запятую или с новой строки."
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
            else:
                items = tuple(item.strip() for item in re.split(r"[,;\n]", text) if item.strip())
                if text.strip() == "-":
                    items = ()
                if field == "queries" and not items:
                    raise ConfigError("Укажите хотя бы один поисковый запрос")
                if len(items) > (10 if field == "queries" else 30) or any(
                    len(item) > 80 for item in items
                ):
                    raise ConfigError("Слишком много значений или слишком длинный текст")
                if field == "area_ids" and any(not item.isdigit() for item in items):
                    raise ConfigError("Для региона нужны числовые ID HeadHunter")
                self._save_search_settings(**{field: items})
        except (ConfigError, ProfileError, TemplateError, OSError) as exc:
            self.api.send_message(chat_id, f"Не сохранил: {exc}. Попробуйте ещё раз или /cancel.")
            return
        del self.pending_edits[chat_id]
        self.api.send_message(chat_id, "Сохранено.")
        if kind == "template":
            self.show_template(chat_id, field.partition(":")[0])
        elif kind == "profile":
            self.show_profile_settings(chat_id)
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
        elif choice == "experience":
            buttons = [
                [{"text": "Любой опыт", "callback_data": "set:experience:any"}],
                [{"text": "Без опыта", "callback_data": "set:experience:entry"}],
                [{"text": "1–3 года", "callback_data": "set:experience:one_three"}],
                [{"text": "Без опыта + 1–3 года", "callback_data": "set:experience:junior"}],
            ]
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
            elif action.startswith("set:experience:"):
                values = {
                    "any": (),
                    "entry": ("noExperience",),
                    "one_three": ("between1And3",),
                    "junior": ("noExperience", "between1And3"),
                }
                choice = action.removeprefix("set:experience:")
                if choice not in values:
                    return
                self._save_search_settings(experience_ids=values[choice])
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
                self.api.send_message(
                    chat_id, "Редактирование отменено.", reply_markup=MAIN_KEYBOARD
                )
            elif text.startswith("/start"):
                self.pending_edits.pop(chat_id, None)
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
                self.show_settings(chat_id)
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
            if action == "scan":
                self.pending_edits.pop(chat_id, None)
                self.scan(chat_id)
            elif action.startswith("new:") and action[4:].isdigit():
                self.show_new(chat_id, int(action[4:]))
            elif action.startswith("history:") and action[8:].isdigit():
                self.show_history(chat_id, int(action[8:]))
            elif action == "menu:main":
                self.pending_edits.pop(chat_id, None)
                self.api.send_message(
                    chat_id, "Главное меню: выберите действие.", reply_markup=MAIN_KEYBOARD
                )
            elif action == "settings":
                self.pending_edits.pop(chat_id, None)
                self.show_settings(chat_id)
            elif action == "settings:search":
                self.pending_edits.pop(chat_id, None)
                self.show_search_settings(chat_id)
            elif action == "settings:profile":
                self.pending_edits.pop(chat_id, None)
                self.show_profile_settings(chat_id)
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

    def scan(self, chat_id: int) -> None:
        self.api.send_message(chat_id, "Ищу вакансии по сохранённым фильтрам…")
        try:
            current_config = replace(self.config, search=self._search_settings())
            result = self.scanner(current_config)
        except (ConfigError, HhApiError, SearchError, PlaywrightError, OSError) as exc:
            self.api.send_message(chat_id, f"Поиск не удался: {_shorten(str(exc), 300)}")
            return
        self.new_items[chat_id] = result.new_items
        if result.errors:
            self.api.send_message(chat_id, "Часть источников недоступна: " + "; ".join(result.errors))
        if not result.new_items:
            self.api.send_message(
                chat_id,
                f"Новых подходящих вакансий нет. Проверено: {result.fetched_count}, "
                f"подошло: {result.accepted_count}. Можно посмотреть уже найденные.",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "📚 Посмотреть найденные", "callback_data": "history:0"}]
                    ]
                },
            )
            return
        self.api.send_message(
            chat_id,
            f"Нашёл {len(result.new_items)} новых вакансий. Проверено: {result.fetched_count}, "
            f"подошло: {result.accepted_count}.",
        )
        self.show_new(chat_id, 0)

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
            "Пришлите публичную HTTPS-ссылку на Tilda-форму работодателя. "
            "Если форм несколько, добавьте через пробел её номер, начиная с 0. "
            "Для отмены /cancel.",
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
            f"Резюме прикреплено: {uploaded}\n"
            f"Обязательные поля для проверки: {missing}\n"
            "Проверьте открытую форму в браузере. Снимок и отчёт сохранены локально. "
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

    def show_new(self, chat_id: int, page: int) -> None:
        items = self.new_items.get(chat_id, [])
        if not items:
            self.api.send_message(chat_id, "Список новых вакансий пуст. Запустите поиск ещё раз.")
            return
        size = self.config.telegram.page_size
        start = page * size
        if start >= len(items):
            self.api.send_message(chat_id, "Это последняя страница новых вакансий.")
            return
        profile = self._optional_profile(chat_id)
        templates = self._optional_templates(chat_id) if profile is not None else DEFAULT_TEMPLATES
        for index, vacancy in enumerate(items[start : start + size], start=start + 1):
            self.api.send_message(
                chat_id,
                vacancy_message(vacancy, profile, index, len(items), templates),
                reply_markup=self._reply_button(vacancy, profile),
                html_mode=True,
            )
        buttons: list[dict[str, str]] = []
        if start + size < len(items):
            buttons.append({"text": "Ещё новые →", "callback_data": f"new:{page + 1}"})
        buttons.append({"text": "📚 Ранее найденные", "callback_data": "history:0"})
        self.api.send_message(
            chat_id, "Продолжить просмотр:", reply_markup={"inline_keyboard": [buttons]}
        )

    def show_history(self, chat_id: int, page: int) -> None:
        size = self.config.telegram.page_size
        with VacancyStore(self.config.database_path) as store:
            total = store.count()
            vacancies = store.recent_vacancies(size, page * size)
        if not vacancies:
            self.api.send_message(
                chat_id,
                "История пока пуста." if total == 0 else "Это последняя страница истории.",
            )
            return
        profile = self._optional_profile(chat_id)
        templates = self._optional_templates(chat_id) if profile is not None else DEFAULT_TEMPLATES
        if page == 0:
            self.api.send_message(chat_id, f"Ранее найденные вакансии: {total}.")
        for index, vacancy in enumerate(vacancies, start=page * size + 1):
            self.api.send_message(
                chat_id,
                vacancy_message(vacancy, profile, index, total, templates),
                reply_markup=self._reply_button(vacancy, profile),
                html_mode=True,
            )
        buttons: list[dict[str, str]] = []
        if page > 0:
            buttons.append({"text": "← Назад", "callback_data": f"history:{page - 1}"})
        if (page + 1) * size < total:
            buttons.append({"text": "Ещё →", "callback_data": f"history:{page + 1}"})
        buttons.append({"text": "🔎 Искать новые", "callback_data": "scan"})
        self.api.send_message(
            chat_id, "Продолжить просмотр:", reply_markup={"inline_keyboard": [buttons]}
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
    offset = _load_offset(offset_path)
    print("Telegram-бот запущен. Остановить: Ctrl+C.", flush=True)
    try:
        while True:
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
