import json

from test_telegram_bot import FakeApi, callback, config, message, vacancy, write_profile

from job_search_automation.application_workflow import ApplicationManager, ReviewSummary
from job_search_automation.storage import VacancyStore
from job_search_automation.telegram_bot import JobTelegramBot


def test_prepare_saves_review_without_submitting_and_refreshes_after_manual_input(tmp_path) -> None:
    settings = config(tmp_path)
    write_profile(settings.profile_path)
    profile_fields = json.loads(settings.profile_path.read_text(encoding="utf-8"))
    profile_fields["email"] = "test@example.test"
    settings.profile_path.write_text(json.dumps(profile_fields), encoding="utf-8")
    with VacancyStore(settings.database_path) as store:
        store.save([vacancy()])
    html = """
    <form class="t-form" onsubmit="window.sent = true; return false">
      <input name="Name" type="text" required>
      <input name="Email" type="email" required>
      <input name="Salary" type="text" required>
      <input name="Consent" type="checkbox" required>
      <button type="submit">Отправить</button>
    </form>
    """

    def configure_page(page):
        page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))

    manager = ApplicationManager(settings, headless=True, configure_page=configure_page)
    try:
        summary = manager.prepare("hh", "123", "https://example.test/form")

        assert summary.missing_required == ("Salary", "Consent")
        assert summary.review_path.is_file()
        assert summary.screenshot_path.is_file()
        review = json.loads(summary.review_path.read_text(encoding="utf-8"))
        assert review["submitted"] is False
        assert "@candidate" not in summary.review_path.read_text(encoding="utf-8")
        assert manager.sessions[("hh", "123")].page.evaluate("window.sent === true") is False

        page = manager.sessions[("hh", "123")].page
        page.locator('input[name="Salary"]').fill("По договорённости")
        page.locator('input[name="Consent"]').check()
        updated = manager.refresh("hh", "123")

        assert updated.review_id != summary.review_id
        assert updated.missing_required == ()
        page.locator("button[type=submit]").click()
        assert page.evaluate("window.sent === true") is False
        with VacancyStore(settings.database_path) as store:
            record = store.application("hh", "123")
            assert record.status == "review_ready"
            assert record.review_id == updated.review_id
    finally:
        manager.close_all()


def test_bot_prompts_for_form_url_and_shows_review(tmp_path) -> None:
    settings = config(tmp_path)
    write_profile(settings.profile_path)
    with VacancyStore(settings.database_path) as store:
        store.save([vacancy()])

    class FakeManager:
        def __init__(self):
            self.calls = []

        def prepare(self, source, source_id, url, *, form_index=None):
            self.calls.append((source, source_id, url, form_index))
            return ReviewSummary(
                source,
                source_id,
                "Python developer",
                url,
                "review-id",
                ("Name",),
                ("Salary",),
                None,
                tmp_path / "review.json",
                tmp_path / "review.png",
            )

    manager = FakeManager()
    api = FakeApi()
    bot = JobTelegramBot(settings, api, application_manager=manager)

    bot.handle_update(callback("appprep:hh:123"))
    assert bot.pending_edits[42] == ("application_url", "hh:123")
    bot.handle_update(message("https://example.test/form 1"))

    assert manager.calls == [("hh", "123", "https://example.test/form", 1)]
    assert bot.pending_edits == {}
    assert "Заявка ещё не отправлена" in api.messages[-1][1]
    assert "Salary" in api.messages[-1][1]
    assert "apprefresh:hh:123" in str(api.messages[-1][2])
