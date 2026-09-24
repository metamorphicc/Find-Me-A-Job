import pytest
from playwright.sync_api import sync_playwright

from job_search_automation.forms import (
    FormProbeError,
    inspect_forms,
    inspect_tilda,
    select_form,
    validate_form_url,
)


def test_probe_reads_fields_without_submitting() -> None:
    html = """
    <form class="t-form" onsubmit="window.sent = true; return false">
      <div class="t-input-group t-input-group_req">
        <label class="t-input-title">Ваше имя</label>
        <input name="Name" type="text" required>
      </div>
      <input name="Email" type="email" placeholder="Эл. почта">
      <input name="file" type="hidden" role="uploadcare-uploader" data-public-key="test-key">
      <button class="t-submit" type="submit">Отправить</button>
    </form>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))
            page.goto("https://example.test/form")

            probe = select_form(inspect_tilda(page))

            assert probe.url == "https://example.test/form"
            assert probe.has_uploadcare is True
            assert probe.has_submit is True
            assert [(field.name, field.kind, field.required) for field in probe.fields] == [
                ("Name", "text", True),
                ("Email", "email", False),
                ("file", "uploadcare", False),
            ]
            assert page.evaluate("window.sent === true") is False
        finally:
            browser.close()


def test_multiple_forms_require_an_explicit_choice() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route(
                "**/*",
                lambda route: route.fulfill(
                    body='<form class="t-form"><button type="submit">Go</button></form>' * 2,
                    content_type="text/html",
                ),
            )
            page.goto("https://example.test/forms")
            probes = inspect_tilda(page)
            with pytest.raises(FormProbeError, match="несколько"):
                select_form(probes)
            assert select_form(probes, 1).form_index == 1
        finally:
            browser.close()


def test_private_form_urls_are_rejected() -> None:
    with pytest.raises(FormProbeError):
        validate_form_url("http://example.test/form")
    with pytest.raises(FormProbeError):
        validate_form_url("https://127.0.0.1/form")


def test_generic_form_ignores_login_and_reads_application_fields() -> None:
    html = """
    <form><input name="username"><input type="password" name="password">
    <button type="submit">Login</button></form>
    <form><label>Name <input name="name" required></label>
    <input name="email" type="email" required><button type="submit">Apply</button></form>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))
            page.goto("https://example.test/apply")
            probe = select_form(inspect_forms(page))
            assert probe.family == "standard"
            assert probe.form_index == 1
            assert [field.name for field in probe.fields] == ["name", "email"]
        finally:
            browser.close()
