from pathlib import Path

from playwright.sync_api import sync_playwright
from test_storage import sample

from job_search_automation.forms import inspect_tilda, select_form
from job_search_automation.profile import CandidateProfile
from job_search_automation.reply_templates import DEFAULT_TEMPLATES
from job_search_automation.tilda_apply import fill_tilda, uploadcare_attach


def profile(resume_path: str) -> CandidateProfile:
    return CandidateProfile(
        "Тестовый кандидат",
        "Изучаю Python.",
        "связь",
        ("Python",),
        "",
        "",
        "test@example.test",
        "+7 900 000 00 00",
        "Новосибирск",
        resume_path,
    )


def test_fill_known_fields_and_leave_unknown_required_fields_for_review(tmp_path) -> None:
    (tmp_path / "cv.pdf").write_bytes(b"%PDF-1.4")
    html = """
    <form class="t-form" onsubmit="window.sent = true; return false">
      <input name="Name" type="text" required>
      <input name="Email" type="email" required>
      <textarea name="Cover letter" required></textarea>
      <input name="Salary" type="text" required>
      <input name="Agreement" type="checkbox" required>
      <input name="Resume" type="file" required>
      <button type="submit">Отправить</button>
    </form>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))
            page.goto("https://example.test/form")
            result = fill_tilda(
                page,
                select_form(inspect_tilda(page)),
                profile("cv.pdf"),
                tmp_path / "profile.json",
                sample(),
                DEFAULT_TEMPLATES[-1],
            )

            assert page.locator('input[name="Name"]').input_value() == "Тестовый кандидат"
            assert page.locator('input[name="Email"]').input_value() == "test@example.test"
            assert "Remote intern" in page.locator("textarea").input_value()
            assert result.missing_required == ("Salary", "Agreement")
            assert result.uploaded_filename == "cv.pdf"
            assert page.evaluate("window.sent === true") is False
        finally:
            browser.close()


def test_uploadcare_requires_confirmed_widget_file_and_cdn(monkeypatch, tmp_path) -> None:
    path = tmp_path / "cv.pdf"
    path.write_bytes(b"%PDF-1.4")
    uuid = "11111111-1111-1111-1111-111111111111"
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"file": uuid}

    def fake_post(url, *, data, files, timeout):
        calls.append((url, data["UPLOADCARE_PUB_KEY"], files["file"][0], timeout))
        return Response()

    monkeypatch.setattr("job_search_automation.tilda_apply.requests.post", fake_post)
    html = """
    <form class="t-form">
      <div class="t-uploadcare">
        <input name="file" role="uploadcare-uploader" data-public-key="public-test">
        <div class="uploadcare--widget" data-status="idle"></div>
        <span class="uploadcare--widget__text"></span>
      </div>
      <button type="submit">Отправить</button>
    </form>
    <script>
      window.uploadcare = {
        Widget: input => ({value: file => {
          input.value = 'https://ucarecdn.com/' + file.uuid + '/';
          input.closest('.t-uploadcare').querySelector('.uploadcare--widget').setAttribute('data-status', 'loaded');
          input.closest('.t-uploadcare').querySelector('.uploadcare--widget__text').textContent = 'cv.pdf';
        }}),
        fileFrom: (_state, uuid) => ({uuid, done: async () => true})
      };
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))
            page.goto("https://example.test/form")
            locator = page.locator('[role="uploadcare-uploader"]')

            uploadcare_attach(page, locator, Path(path))

            assert calls == [("https://upload.uploadcare.com/base/", "public-test", "cv.pdf", 90)]
            assert uuid in locator.input_value()
        finally:
            browser.close()
