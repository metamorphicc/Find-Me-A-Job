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
from job_search_automation.reply_templates import load_templates, templates_path
from job_search_automation.search import ScanResult
from job_search_automation.storage import VacancyStore
from job_search_automation.telegram_bot import MAIN_KEYBOARD, JobTelegramBot


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
        self.photos = []
        self.edits = []

    def send_message(self, chat_id, text, *, reply_markup=None, html_mode=False) -> int:
        self.messages.append((chat_id, text, reply_markup, html_mode))
        return len(self.messages)

    def edit_message(
        self, chat_id, message_id, text, *, reply_markup=None, html_mode=False
    ) -> None:
        self.edits.append((chat_id, message_id, text))
        self.messages[message_id - 1] = (chat_id, text, reply_markup, html_mode)

    def answer_callback(self, callback_id, text="") -> None:
        self.callbacks.append((callback_id, text))

    def send_photo(self, chat_id, path) -> None:
        self.photos.append((chat_id, path))


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
    assert len(api.messages) == 1
    assert next(markup for _, _, markup, mode in api.messages if mode)["inline_keyboard"][0][0][
        "callback_data"
    ] == "history:0"

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
    assert any(
        button["callback_data"] == "history:1"
        for row in api.messages[-1][2]["inline_keyboard"] for button in row
    )

    bot.handle_update(callback("history:1"))
    cards = [text for _, text, _, mode in api.messages if mode]
    assert len(cards) == 2
    assert "https://hh.ru/vacancy/" in cards[1]


def test_new_cards_use_one_message_and_arrows_edit_it(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    first = vacancy("1")
    second = replace(vacancy("2"), title="Backend developer")
    bot = JobTelegramBot(settings, api)
    bot.scan(42, result=ScanResult([first, second], 2, 2, "test"))

    assert len(api.messages) == 1
    assert "Junior Python" in api.messages[0][1]
    update = callback("new:1")
    update["callback_query"]["message"]["message_id"] = 1
    bot.handle_update(update)

    assert len(api.messages) == 1
    assert api.edits[0][1] == 1
    assert "Backend developer" in api.messages[0][1]
    buttons = api.messages[0][2]["inline_keyboard"]
    assert any(button["callback_data"] == "new:0" for row in buttons for button in row)


def test_history_arrows_edit_same_card(tmp_path) -> None:
    settings = config(tmp_path)
    with VacancyStore(settings.database_path) as store:
        store.save([vacancy("1"), replace(vacancy("2"), title="Backend developer")])
    api = FakeApi()
    bot = JobTelegramBot(settings, api)
    bot.show_history(42, 0)
    update = callback("history:1")
    update["callback_query"]["message"]["message_id"] = 1
    bot.handle_update(update)

    assert len(api.messages) == 1
    assert len(api.edits) == 1
    assert "Junior Python" in api.messages[0][1]


def test_history_respects_current_professional_categories(tmp_path) -> None:
    settings = config(tmp_path)
    with VacancyStore(settings.database_path) as store:
        store.save([
            replace(vacancy("1"), title="Бармен", categories=()),
            replace(vacancy("2"), title="Backend developer", categories=("software",)),
        ])
    api = FakeApi()
    bot = JobTelegramBot(settings, api)
    bot._save_search_settings(categories=("software",))
    bot.show_history(42, 0)

    assert "Backend developer" in api.messages[-1][1]
    assert "Бармен" not in api.messages[-1][1]
    assert "1/1" in api.messages[-1][1]


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


def test_bot_edits_precise_search_filters(tmp_path) -> None:
    settings = config(tmp_path)
    bot = JobTelegramBot(settings, FakeApi())

    bot.handle_update(callback("toggle:role:96"))
    bot.handle_update(callback("toggle:employment:FULL"))
    bot.handle_update(callback("toggle:work_schedule:FIVE_ON_TWO_OFF"))
    bot.handle_update(callback("toggle:experience:between3And6"))
    bot.handle_update(callback("edit:search:title_keywords"))
    bot.handle_update(message("developer, разработчик"))
    bot.handle_update(callback("edit:search:salary_min"))
    bot.handle_update(message("100000"))
    bot.handle_update(callback("toggle:salary_required"))

    saved = bot._search_settings()
    assert saved.role_ids == ("96",)
    assert saved.employment_forms == ("FULL",)
    assert saved.work_schedules == ("FIVE_ON_TWO_OFF",)
    assert saved.experience_ids == ("between3And6",)
    assert saved.title_keywords == ("developer", "разработчик")
    assert saved.salary_min == 100000
    assert saved.salary_required is True


def test_settings_back_returns_to_main_menu(tmp_path) -> None:
    api = FakeApi()
    bot = JobTelegramBot(config(tmp_path), api)

    bot.handle_update(message("/settings"))
    assert api.messages[-1][2]["inline_keyboard"][-1][0] == {
        "text": "← Назад",
        "callback_data": "menu:main",
    }

    bot.pending_edits[42] = ("search", "queries")
    bot.handle_update(callback("menu:main"))

    assert bot.pending_edits == {}
    assert api.messages[-1][2] == MAIN_KEYBOARD
    assert "Главное меню" in api.messages[-1][1]


def test_bot_edits_role_template_and_uses_it_for_new_and_old_vacancies(tmp_path) -> None:
    settings = config(tmp_path)
    write_profile(settings.profile_path)
    api = FakeApi()

    def scan(_):
        with VacancyStore(settings.database_path) as store:
            new_items = store.save([vacancy()])
        return ScanResult(new_items, 1, 1, "api")

    bot = JobTelegramBot(settings, api, scanner=scan)
    bot.handle_update(message("/settings"))
    assert "templates" in str(api.messages[-1][2])

    bot.handle_update(callback("templates"))
    bot.handle_update(callback("templates:python"))
    bot.handle_update(callback("edit:template:python:body"))
    bot.handle_update(message("Здравствуйте, я {name}. {about} {contact_line}"))

    path = templates_path(settings.database_path)
    assert path.is_file()
    assert next(item for item in load_templates(path) if item.key == "python").body.startswith(
        "Здравствуйте, я"
    )

    bot.handle_update(message("/scan"))
    card = next(text for _, text, _, mode in api.messages if mode)
    assert "Шаблон: Python" in card
    assert "Здравствуйте, я Иван" in card
    assert "https://hh.ru/vacancy/123" in card

    bot.handle_update(message("/history"))
    historical_card = [text for _, text, _, mode in api.messages if mode][-1]
    assert "Шаблон: Python" in historical_card
    assert "Здравствуйте, я Иван" in historical_card

    card_markup = [markup for _, _, markup, mode in api.messages if mode][-1]
    assert card_markup["inline_keyboard"][0][0]["callback_data"] == "reply:hh:123"
    bot.handle_update(callback("reply:hh:123"))
    choices = api.messages[-1][2]["inline_keyboard"]
    assert any(button[0]["callback_data"] == "replypick:hh:123:general" for button in choices)
    bot.handle_update(callback("replypick:hh:123:general"))
    alternative = api.messages[-1][1]
    assert "шаблон «Общий»" in alternative
    assert "Хочу откликнуться" in alternative
    assert "https://hh.ru/vacancy/123" in alternative


def test_invalid_template_edit_keeps_previous_text(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    bot = JobTelegramBot(settings, api)

    bot.handle_update(callback("edit:template:python:body"))
    bot.handle_update(message("{unknown}"))

    assert "Не сохранил" in api.messages[-1][1]
    assert bot.pending_edits[42] == ("template", "python:body")
    assert not templates_path(settings.database_path).exists()


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


def test_profile_setup_guides_required_fields_without_inventing_optional_facts(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    bot = JobTelegramBot(settings, api)

    bot.handle_update(message("/profile"))
    assert settings.profile_path.is_file()
    assert "Заполнить основу" in str(api.messages[-1][2])
    bot.handle_update(callback("profile:setup"))
    assert bot.pending_edits[42] == ("profile", "name")
    bot.handle_update(message("Иван"))
    assert bot.pending_edits[42] == ("profile", "about")
    bot.handle_update(message("Пишу на Python"))
    assert bot.pending_edits[42] == ("profile", "contact")
    bot.handle_update(message("@candidate"))

    raw = json.loads(settings.profile_path.read_text(encoding="utf-8"))
    assert raw["name"] == "Иван"
    assert raw["email"] == ""
    assert 42 not in bot.profile_setup
    assert "Готовый текст отклика включён" in api.messages[-1][1]


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


def test_bot_can_enable_sources_and_choose_only_freelance(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    bot = JobTelegramBot(settings, api)

    bot.handle_update(callback("choose:sources"))
    assert "Remotive" in str(api.messages[-1][2])
    bot.handle_update(callback("toggle:source:remotive"))
    bot.handle_update(callback("toggle:kind:job"))

    saved = bot._search_settings()
    assert saved.sources == ("hh", "remotive")
    assert saved.kinds == ("freelance",)


def test_bot_switches_professional_categories(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    bot = JobTelegramBot(settings, api)

    bot.handle_update(callback("choose:categories"))
    assert "Разработка и тестирование" in str(api.messages[-1][2])
    bot.handle_update(callback("toggle:category:software"))
    assert bot._search_settings().categories == ("software",)
    bot.handle_update(callback("toggle:category:it_ops"))
    assert bot._search_settings().categories == ("software", "it_ops")
    bot.handle_update(callback("set:categories:any"))
    assert bot._search_settings().categories == ()


def test_non_hh_opportunity_has_reply_button(tmp_path) -> None:
    settings = config(tmp_path)
    write_profile(settings.profile_path)
    api = FakeApi()
    item = replace(vacancy(), source="wwr", source_id="abcdef1234", kind="freelance")
    with VacancyStore(settings.database_path) as store:
        store.save([item])
    bot = JobTelegramBot(settings, api)
    bot.handle_update(message("/history"))
    card_markup = [markup for _, _, markup, mode in api.messages if mode][-1]
    assert card_markup["inline_keyboard"][0][0]["callback_data"] == "reply:wwr:abcdef1234"


def test_bot_edits_custom_fact_and_template_form_value(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    bot = JobTelegramBot(settings, api)
    bot.handle_update(callback("edit:fact:new"))
    bot.handle_update(message("GitHub = https://github.com/example"))
    assert json.loads(settings.profile_path.read_text(encoding="utf-8"))["facts"]["GitHub"]

    bot.handle_update(callback("edit:template:python:form_value"))
    bot.handle_update(message("Salary = 100000"))
    template = next(item for item in load_templates(templates_path(settings.database_path)) if item.key == "python")
    assert template.form_values == (("Salary", "100000"),)


def test_shared_scheduled_result_does_not_repeat_search(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    calls = []

    def scan(_config):
        calls.append(1)
        return ScanResult([vacancy()], 1, 1, "test")

    bot = JobTelegramBot(settings, api, scanner=scan)
    result = bot.scan(42)
    bot.scan(43, result=result)
    assert len(calls) == 1
    assert len([text for chat_id, text, _, mode in api.messages if chat_id == 43 and mode]) == 1


def test_bot_can_set_daily_search_time(tmp_path) -> None:
    settings = config(tmp_path)
    api = FakeApi()
    bot = JobTelegramBot(settings, api)
    bot.handle_update(callback("settings:schedule"))
    bot.handle_update(callback("edit:schedule:time"))
    bot.handle_update(message("08:30"))
    bot.handle_update(callback("toggle:schedule"))
    assert bot._schedule_settings().enabled is True
    assert bot._schedule_settings().time == "08:30"
