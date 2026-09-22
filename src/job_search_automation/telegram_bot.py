from __future__ import annotations

import html
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import Error as PlaywrightError

from job_search_automation.config import AppConfig, ConfigError
from job_search_automation.hh import HhApiError
from job_search_automation.models import Vacancy
from job_search_automation.profile import CandidateProfile, ProfileError, load_profile
from job_search_automation.reporting import _salary
from job_search_automation.search import ScanResult, scan_vacancies
from job_search_automation.storage import VacancyStore

SEARCH_BUTTON = "🔎 Искать вакансии"
HISTORY_BUTTON = "📚 Ранее найденные"
MAIN_KEYBOARD = {
    "keyboard": [[{"text": SEARCH_BUTTON}, {"text": HISTORY_BUTTON}]],
    "resize_keyboard": True,
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


def vacancy_message(vacancy: Vacancy, profile: CandidateProfile, index: int, total: int) -> str:
    title = html.escape(vacancy.title)
    company = html.escape(vacancy.company)
    area = html.escape(vacancy.area)
    summary = html.escape(_shorten(vacancy.summary, 320))
    url = html.escape(vacancy.url, quote=True)
    application = html.escape(profile.application_text(vacancy))
    return (
        f"<b>{index}/{total} · {title}</b>\n"
        f"{company} · {area}\n"
        f"Удалённо · Опыт: {html.escape(vacancy.experience)} · "
        f"Зарплата: {html.escape(_salary(vacancy))}\n"
        f"{summary or 'Краткое описание отсутствует.'}\n\n"
        f'<a href="{url}">Открыть вакансию ↗</a>\n'
        f"{url}\n\n"
        f"<b>Готовый текст отклика:</b>\n<pre>{application}</pre>"
    )


class JobTelegramBot:
    def __init__(
        self,
        config: AppConfig,
        api: TelegramApi,
        *,
        scanner: Callable[[AppConfig], ScanResult] = scan_vacancies,
    ) -> None:
        self.config = config
        self.api = api
        self.scanner = scanner
        self.new_items: dict[int, list[Vacancy]] = {}

    def _allowed(self, user_id: int, chat_id: int, chat_type: str) -> bool:
        if chat_type != "private":
            return False
        if user_id not in self.config.telegram.allowed_user_ids:
            self.api.send_message(
                chat_id,
                f"Доступ закрыт. Ваш Telegram ID: {user_id}. "
                "Добавьте его в telegram.allowed_user_ids локального config.toml.",
            )
            return False
        return True

    def _profile(self, chat_id: int) -> CandidateProfile | None:
        try:
            return load_profile(self.config.profile_path)
        except ProfileError as exc:
            self.api.send_message(chat_id, str(exc))
            return None

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
            if text.startswith("/start"):
                self.api.send_message(
                    chat_id,
                    "Нажмите «Искать вакансии», чтобы проверить новые подходящие позиции. "
                    "История хранится на этом компьютере.",
                    reply_markup=MAIN_KEYBOARD,
                )
            elif text == SEARCH_BUTTON or text.startswith("/scan"):
                self.scan(chat_id)
            elif text == HISTORY_BUTTON or text.startswith("/history"):
                self.show_history(chat_id, 0)
            else:
                self.api.send_message(
                    chat_id, "Используйте кнопки поиска и истории.", reply_markup=MAIN_KEYBOARD
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
                or user_id not in self.config.telegram.allowed_user_ids
            ):
                self.api.answer_callback(callback_id, "Нет доступа")
                return
            self.api.answer_callback(callback_id)
            action = str(callback.get("data") or "")
            if action == "scan":
                self.scan(chat_id)
            elif action.startswith("new:") and action[4:].isdigit():
                self.show_new(chat_id, int(action[4:]))
            elif action.startswith("history:") and action[8:].isdigit():
                self.show_history(chat_id, int(action[8:]))

    def scan(self, chat_id: int) -> None:
        if self._profile(chat_id) is None:
            return
        self.api.send_message(chat_id, "Ищу вакансии по настройкам из config.toml…")
        try:
            result = self.scanner(self.config)
        except (HhApiError, PlaywrightError, OSError) as exc:
            self.api.send_message(chat_id, f"Поиск не удался: {_shorten(str(exc), 300)}")
            return
        self.new_items[chat_id] = result.new_items
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

    def show_new(self, chat_id: int, page: int) -> None:
        profile = self._profile(chat_id)
        if profile is None:
            return
        items = self.new_items.get(chat_id, [])
        if not items:
            self.api.send_message(chat_id, "Список новых вакансий пуст. Запустите поиск ещё раз.")
            return
        size = self.config.telegram.page_size
        start = page * size
        if start >= len(items):
            self.api.send_message(chat_id, "Это последняя страница новых вакансий.")
            return
        for index, vacancy in enumerate(items[start : start + size], start=start + 1):
            self.api.send_message(
                chat_id,
                vacancy_message(vacancy, profile, index, len(items)),
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
        profile = self._profile(chat_id)
        if profile is None:
            return
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
        if page == 0:
            self.api.send_message(chat_id, f"Ранее найденные вакансии: {total}.")
        for index, vacancy in enumerate(vacancies, start=page * size + 1):
            self.api.send_message(
                chat_id, vacancy_message(vacancy, profile, index, total), html_mode=True
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
    while True:
        try:
            updates = api.get_updates(offset)
        except TelegramApiError as exc:
            if "Conflict" in str(exc):
                raise TelegramApiError("Другой экземпляр бота уже запущен") from exc
            if "Unauthorized" in str(exc):
                raise TelegramApiError("Telegram отклонил токен бота. Проверьте bot_token") from exc
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
