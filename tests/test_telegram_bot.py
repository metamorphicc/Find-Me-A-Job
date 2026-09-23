import json
from dataclasses import replace

from job_search_automation.config import (
    AppConfig,
    HhConfig,
    SearchConfig,
    TelegramConfig,
    load_config,
    load_search_settings,
    save_search_settings,
    search_settings_path,
)
from job_search_automation.models import Vacancy
from job_search_automation.search import ScanResult
from job_search_automation.storage import VacancyStore
from job_search_automation.telegram_bot import JobTelegramBot


def vacancy(source_id: str = "123") -> Vacancy:
    return Vacancy(
        source="hh",
        source_id=source_id,
        title="Junior Python <Developer>",
        company="Example & Co",
        url=f"https://hh.ru/vacancy/{source_id}",
        area="Москва",
        published_at="2026-09-22T00:00:00+0300",
        work_formats=("REMOTE",),
        experience="Нет опыта",
        employment="Полная занятость",
        salary_from=None,
        salary_to=None,
        salary_currency=None,
        salary_gross=None,
        summary="Python & SQL",
        query="junior",
    )


def config(tmp_path) -> AppConfig:
    return AppConfig(
        search=SearchConfig(
            queries=("junior",),
            excluded_keywords=(),
            area_ids=("113",),
            experience_ids=(),
            remote_only=True,
            strict_remote=True,
            days=7,
            per_query=20,
        ),
        hh=HhConfig("https://api.hh.ru", "test", 20),
        database_path=tmp_path / "data" / "jobs.db",
        reports_dir=tmp_path / "reports",
        telegram=TelegramConfig("test-token", (42,), 1),
        profile_path=tmp_path / "profile.json",
    )


class FakeApi:
    def __init__(self) -> None:
        self.messages = []
        self.callbacks = []

    def send_message(self, chat_id, text, *, reply_markup=None, html_mode=False) -> None:
        self.messages.append((chat_id, text, reply_markup, html_mode))

    def answer_callback(self, callback_id, text="") -> None:
        self.callbacks.append((callback_id, text))


def message(text: str, user_id: int = 42) -> dict:
    return {
        "message": {
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id},
            "text": text,
        }
    }


def callback(data: str) -> dict:
    return {
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 42},
            "message": {"chat": {"id": 42, "type": "private"}},
            "data": data,
        }
    }


def write_profile(path) -> None:
    path.write_text(
        json.dumps(
            {
                "name": "Иван",
                "about": "Изучаю Python и делал учебные проекты.",
                "contact": "@candidate",
                "skills": ["Python", "SQL"],
                "resume_url": "https://example.test/resume",
                "portfolio_url": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_bot_searches_without_profile_and_sends_vacancy_link(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    calls = []

    def scan(_):
        calls.append(True)
        with VacancyStore(settings.database_path) as store:
            new_items = store.save([vacancy()])
        return ScanResult(new_items, 1, 1, "api")

    bot = JobTelegramBot(settings, api, scanner=scan)

    bot.handle_update(message("/scan"))

    assert calls == [True]
    card = next(text for _, text, _, mode in api.messages if mode)
    assert "https://hh.ru/vacancy/123" in card
    assert "Готовый текст отклика" not in card

    bot.handle_update(message("/history"))
    historical_card = [text for _, text, _, mode in api.messages if mode][-1]
    assert "https://hh.ru/vacancy/123" in historical_card
    assert "Готовый текст отклика" not in historical_card


def test_invalid_optional_profile_does_not_block_vacancy_history(tmp_path) -> None:
    settings = config(tmp_path)
    settings.profile_path.write_text('{"name": ""}', encoding="utf-8")
    with VacancyStore(settings.database_path) as store:
        store.save([vacancy()])
    api = FakeApi()
    bot = JobTelegramBot(settings, api)

    bot.handle_update(message("/history"))

    assert any("Текст отклика пока недоступен" in text for _, text, _, _ in api.messages)
    assert any("https://hh.ru/vacancy/123" in text for _, text, _, _ in api.messages)


def test_unauthorized_user_cannot_search_or_read_history(tmp_path) -> None:
    settings = config(tmp_path)
    write_profile(settings.profile_path)
    api = FakeApi()
    calls = []
    bot = JobTelegramBot(settings, api, scanner=lambda _: calls.append(True))

    bot.handle_update(message("/scan", user_id=99))
    bot.handle_update(message("/history", user_id=99))

    assert calls == []
    assert all("Доступ закрыт" in item[1] for item in api.messages)


def test_new_and_historical_vacancies_have_link_and_filled_reply(tmp_path) -> None:
    settings = config(tmp_path)
    write_profile(settings.profile_path)
    api = FakeApi()
    item = vacancy()

    def scan(_):
        with VacancyStore(settings.database_path) as store:
            new_items = store.save([item])
        return ScanResult(new_items, 1, 1, "api")

    bot = JobTelegramBot(settings, api, scanner=scan)
    bot.handle_update(message("/scan"))
    card = next(text for _, text, _, mode in api.messages if mode)

    assert "https://hh.ru/vacancy/123" in card
    assert "Иван" in card
    assert "@candidate" in card
    assert "Junior Python &lt;Developer&gt;" in card
    assert "Example &amp; Co" in card

    bot.handle_update(message("/scan"))
    assert "Новых подходящих вакансий нет" in api.messages[-1][1]
    assert api.messages[-1][2]["inline_keyboard"][0][0]["callback_data"] == "history:0"

    bot.handle_update(callback("history:0"))
    historical_card = [text for _, text, _, mode in api.messages if mode][-1]
    assert "https://hh.ru/vacancy/123" in historical_card
    assert "Иван" in historical_card
    assert api.callbacks == [("callback-1", "")]


def test_old_vacancies_are_paginated_from_local_database(tmp_path) -> None:
    settings = config(tmp_path)
    write_profile(settings.profile_path)
    with VacancyStore(settings.database_path) as store:
        store.save([vacancy("1"), vacancy("2")])
    api = FakeApi()
    bot = JobTelegramBot(settings, api)

    bot.handle_update(message("/history"))
    assert api.messages[-1][2]["inline_keyboard"][0][0]["callback_data"] == "history:1"

    bot.handle_update(callback("history:1"))
    cards = [text for _, text, _, mode in api.messages if mode]
    assert len(cards) == 2
    assert "https://hh.ru/vacancy/" in cards[1]


def test_bot_edits_search_filters_and_uses_them_for_next_scan(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    used_searches = []

    def scan(current):
        used_searches.append(current.search)
        return ScanResult([], 0, 0, "api")

    bot = JobTelegramBot(settings, api, scanner=scan)
    bot.handle_update(message("/settings"))
    assert "settings:search" in str(api.messages[-1][2])

    bot.handle_update(callback("edit:search:queries"))
    bot.handle_update(message("Python-разработчик, backend стажёр"))
    bot.handle_update(callback("toggle:remote"))
    bot.handle_update(callback("set:days:14"))
    bot.handle_update(callback("set:experience:entry"))
    bot.handle_update(message("/scan"))

    assert used_searches[0].queries == ("Python-разработчик", "backend стажёр")
    assert used_searches[0].remote_only is False
    assert used_searches[0].strict_remote is False
    assert used_searches[0].days == 14
    assert used_searches[0].experience_ids == ("noExperience",)
    saved = load_search_settings(settings.search, search_settings_path(settings.database_path))
    assert saved == used_searches[0]


def test_bot_edits_candidate_profile_and_fills_history_reply(tmp_path) -> None:
    settings = config(tmp_path)
    with VacancyStore(settings.database_path) as store:
        store.save([vacancy()])
    api = FakeApi()
    bot = JobTelegramBot(settings, api)

    bot.handle_update(callback("edit:profile:name"))
    bot.handle_update(message("Иван"))
    bot.handle_update(callback("edit:profile:about"))
    bot.handle_update(message("Изучаю Python и делал учебные проекты."))
    bot.handle_update(callback("edit:profile:contact"))
    bot.handle_update(message("@candidate"))
    bot.handle_update(callback("edit:profile:skills"))
    bot.handle_update(message("Python, SQL"))
    bot.handle_update(callback("edit:profile:resume_url"))
    bot.handle_update(message("https://example.test/resume"))
    bot.handle_update(message("/history"))

    card = [text for _, text, _, mode in api.messages if mode][-1]
    assert "Готовый текст отклика" in card
    assert "Иван" in card
    assert "@candidate" in card
    assert "Python, SQL" in card
    assert "https://example.test/resume" in card
    assert json.loads(settings.profile_path.read_text(encoding="utf-8"))["name"] == "Иван"


def test_invalid_filter_edit_keeps_previous_settings(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    bot = JobTelegramBot(settings, api)

    bot.handle_update(callback("edit:search:queries"))
    bot.handle_update(message("-"))

    assert "Не сохранил" in api.messages[-1][1]
    assert bot.pending_edits[42] == ("search", "queries")
    assert bot._search_settings().queries == ("junior",)
    bot.handle_update(message("/cancel"))
    assert 42 not in bot.pending_edits


def test_unauthorized_user_cannot_edit_search_settings(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    bot = JobTelegramBot(settings, api)
    update = callback("toggle:remote")
    update["callback_query"]["from"]["id"] = 99
    update["callback_query"]["message"]["chat"]["id"] = 99

    bot.handle_update(update)

    assert bot._search_settings().remote_only is True
    assert not search_settings_path(settings.database_path).exists()
    assert api.callbacks == [("callback-1", "Нет доступа")]


def test_saved_bot_filters_are_loaded_by_cli_config_after_restart(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[search]\nqueries = ["junior"]\n', encoding="utf-8")
    original = load_config(config_path)
    updated = replace(original.search, queries=("Python",), days=14)
    save_search_settings(updated, search_settings_path(original.database_path))

    restarted = load_config(config_path)

    assert restarted.search.queries == ("Python",)
    assert restarted.search.days == 14
