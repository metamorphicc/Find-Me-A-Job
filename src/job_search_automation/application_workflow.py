from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from job_search_automation.config import AppConfig
from job_search_automation.forms import (
    FormProbe,
    FormProbeError,
    inspect_tilda,
    select_form,
    validate_form_url,
)
from job_search_automation.models import Vacancy
from job_search_automation.profile import load_profile
from job_search_automation.reply_templates import load_templates, select_template, templates_path
from job_search_automation.storage import ApplicationStateError, VacancyStore
from job_search_automation.tilda_apply import FillResult, fill_tilda


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    source: str
    source_id: str
    title: str
    form_url: str
    review_id: str
    filled: tuple[str, ...]
    missing_required: tuple[str, ...]
    uploaded_filename: str | None
    review_path: Path
    screenshot_path: Path


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    status: str
    evidence_path: Path
    screenshot_path: Path | None


@dataclass(slots=True)
class _Session:
    playwright: Playwright
    browser: Browser
    page: Page
    vacancy: Vacancy
    probe: FormProbe
    filled: FillResult
    review_id: str = ""
    fingerprint: str = ""


class ApplicationManager:
    def __init__(
        self,
        config: AppConfig,
        *,
        headless: bool = False,
        configure_page: Callable[[Page], None] | None = None,
        success_timeout_ms: int = 15_000,
    ) -> None:
        self.config = config
        self.headless = headless
        self.configure_page = configure_page
        self.success_timeout_ms = success_timeout_ms
        self.sessions: dict[tuple[str, str], _Session] = {}

    def prepare(
        self, source: str, source_id: str, url: str, *, form_index: int | None = None
    ) -> ReviewSummary:
        target = validate_form_url(url)
        profile = load_profile(self.config.profile_path)
        with VacancyStore(self.config.database_path) as store:
            vacancy = store.get_vacancy(source, source_id)
            record = store.application(source, source_id)
        if vacancy is None:
            raise ApplicationStateError("Вакансия не найдена в истории")
        if record and record.status in {"attempted", "submitted"}:
            raise ApplicationStateError(
                "Заявка уже отправлена или результат прежней попытки неясен"
            )
        template = select_template(
            vacancy, load_templates(templates_path(self.config.database_path))
        )
        self.close(source, source_id)
        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch(headless=self.headless)
            page = browser.new_page()
            if self.configure_page:
                self.configure_page(page)
            page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            probe = select_form(inspect_tilda(page), form_index)
            page.evaluate(
                """index => {
                  const form = document.querySelectorAll('form.t-form')[index];
                  window.__jobApplicationApproved = false;
                  const block = event => {
                    if (!window.__jobApplicationApproved) {
                      event.preventDefault();
                      event.stopImmediatePropagation();
                    }
                  };
                  form.addEventListener('submit', block, true);
                  form.querySelectorAll('button[type=submit], input[type=submit], .t-submit')
                    .forEach(button => button.addEventListener('click', block, true));
                }""",
                probe.form_index,
            )
            with VacancyStore(self.config.database_path) as store:
                store.record_inspection(source, source_id, probe.url)
            filled = fill_tilda(page, probe, profile, self.config.profile_path, vacancy, template)
            session = _Session(playwright, browser, page, vacancy, probe, filled)
            self.sessions[(source, source_id)] = session
            return self._save_review(session)
        except Exception:
            if (source, source_id) in self.sessions:
                self.close(source, source_id)
            else:
                if "browser" in locals():
                    browser.close()
                playwright.stop()
            raise

    def refresh(self, source: str, source_id: str) -> ReviewSummary:
        session = self.sessions.get((source, source_id))
        if session is None:
            raise ApplicationStateError("Браузерная сессия закрыта; подготовьте заявку заново")
        probes = inspect_tilda(session.page)
        current = select_form(probes, session.probe.form_index)
        if current.url != session.probe.url or current.fields != session.probe.fields:
            raise FormProbeError("Форма изменилась; подготовьте заявку заново")
        return self._save_review(session)

    def _form_state(self, session: _Session) -> tuple[str, tuple[str, ...]]:
        form = session.page.locator("form.t-form").nth(session.probe.form_index)
        values = form.evaluate(
            """form => Array.from(form.elements).map(el => {
              if (el.type === 'file') return Array.from(el.files || []).map(file => file.name);
              if (el.type === 'checkbox' || el.type === 'radio') return el.checked;
              return el.value || '';
            })"""
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                [session.page.url, session.probe.form_index, values], ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
        missing = []
        for field in session.probe.fields:
            if not field.required:
                continue
            locator = form.locator("input, textarea, select").nth(field.index)
            label = field.label or field.name or f"поле {field.index + 1}"
            if field.kind in {"checkbox", "radio"}:
                present = locator.is_checked()
            elif field.kind == "file":
                present = bool(locator.evaluate("el => el.files?.length"))
            elif field.kind == "uploadcare":
                present = locator.evaluate(
                    """input => {
                      const holder = input.closest('.t-uploadcare') || input.parentElement;
                      const status = holder?.querySelector('.uploadcare--widget')?.getAttribute('data-status');
                      const text = holder?.querySelector('.uploadcare--widget__text')?.textContent || '';
                      return status === 'loaded' && Boolean(text.trim()) &&
                        input.value.includes('ucarecdn.com/');
                    }"""
                )
            else:
                present = bool(locator.input_value().strip())
            if not present:
                missing.append(label)
        return fingerprint, tuple(missing)

    def _save_review(self, session: _Session) -> ReviewSummary:
        fingerprint, missing = self._form_state(session)
        review_id = secrets.token_hex(8)
        root = self.config.profile_path.resolve().parent
        slug = f"{session.vacancy.source}_{session.vacancy.source_id}_{review_id}"
        review_path = root / "artifacts" / "tilda" / f"{slug}_review.json"
        screenshot_path = root / "screenshots" / "tilda" / f"{slug}_before_submit.png"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        session.page.screenshot(path=str(screenshot_path), full_page=True)
        summary = ReviewSummary(
            source=session.vacancy.source,
            source_id=session.vacancy.source_id,
            title=session.vacancy.title,
            form_url=session.probe.url,
            review_id=review_id,
            filled=session.filled.filled,
            missing_required=missing,
            uploaded_filename=session.filled.uploaded_filename,
            review_path=review_path,
            screenshot_path=screenshot_path,
        )
        temporary = review_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    **{
                        key: value
                        for key, value in asdict(summary).items()
                        if key not in {"review_path", "screenshot_path"}
                    },
                    "fingerprint": fingerprint,
                    "submitted": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, review_path)
        with VacancyStore(self.config.database_path) as store:
            store.record_review(
                summary.source,
                summary.source_id,
                review_id,
                str(review_path),
                str(screenshot_path),
            )
        session.review_id = review_id
        session.fingerprint = fingerprint
        return summary

    def submit(self, source: str, source_id: str, review_id: str) -> SubmissionOutcome:
        session = self.sessions.get((source, source_id))
        if session is None or session.review_id != review_id:
            raise ApplicationStateError("Отзыв устарел; откройте актуальную проверку заявки")
        with VacancyStore(self.config.database_path) as store:
            record = store.application(source, source_id)
        if record is None or record.status != "review_ready" or record.review_id != review_id:
            raise ApplicationStateError("Заявка не готова к отправке")
        current = select_form(inspect_tilda(session.page), session.probe.form_index)
        if current.url != session.probe.url or current.fields != session.probe.fields:
            raise ApplicationStateError("Форма изменилась после проверки")
        fingerprint, missing = self._form_state(session)
        if missing:
            raise ApplicationStateError("Остались незаполненные обязательные поля")
        if fingerprint != session.fingerprint:
            raise ApplicationStateError("Данные формы изменились; нажмите «Проверить снова»")
        form = session.page.locator("form.t-form").nth(session.probe.form_index)
        submit_button = form.locator("button[type=submit], input[type=submit], .t-submit")
        if submit_button.count() != 1 or not submit_button.first.is_visible():
            raise ApplicationStateError("Не найдена однозначная кнопка отправки")

        with VacancyStore(self.config.database_path) as store:
            store.record_attempt(source, source_id, review_id)
        status = "ambiguous"
        success_text = ""
        try:
            session.page.evaluate("window.__jobApplicationApproved = true")
            submit_button.click(timeout=10_000)
            session.page.wait_for_function(
                """({index, originalUrl}) => {
                  const form = document.querySelectorAll('form.t-form')[index];
                  const successBox = Array.from(form?.querySelectorAll('.t-form__successbox') || [])
                    .some(box => box.getClientRects().length && box.textContent.trim());
                  const thankYouPage = location.href !== originalUrl &&
                    /спасибо|thank you|заявка отправлена|application submitted/i
                      .test(document.body?.innerText || '');
                  return successBox || thankYouPage;
                }""",
                arg={"index": session.probe.form_index, "originalUrl": session.probe.url},
                timeout=self.success_timeout_ms,
            )
            success_text = session.page.evaluate(
                """index => {
                  const form = document.querySelectorAll('form.t-form')[index];
                  const box = Array.from(form?.querySelectorAll('.t-form__successbox') || [])
                    .find(item => item.getClientRects().length && item.textContent.trim());
                  if (box) return box.textContent.trim();
                  return (document.body?.innerText || '')
                    .match(/спасибо|thank you|заявка отправлена|application submitted/i)?.[0] || '';
                }""",
                session.probe.form_index,
            )
            if success_text.strip():
                status = "submitted"
        except PlaywrightError:
            # Once the final click was attempted, a missing success signal is ambiguous.
            status = "ambiguous"
        finally:
            try:
                session.page.evaluate("window.__jobApplicationApproved = false")
            except PlaywrightError:
                status = "ambiguous"

        root = self.config.profile_path.resolve().parent
        slug = f"{source}_{source_id}_{review_id}"
        evidence_path = root / "artifacts" / "tilda" / f"{slug}_submission.json"
        screenshot_path = root / "screenshots" / "tilda" / f"{slug}_after_submit.png"
        try:
            session.page.screenshot(path=str(screenshot_path), full_page=True)
        except PlaywrightError:
            screenshot_path = None
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = evidence_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "source": source,
                    "source_id": source_id,
                    "review_id": review_id,
                    "status": status,
                    "final_url": session.page.url,
                    "success_text": success_text[:300],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, evidence_path)
        if status == "submitted":
            with VacancyStore(self.config.database_path) as store:
                store.record_submitted(source, source_id, str(evidence_path))
        return SubmissionOutcome(status, evidence_path, screenshot_path)

    def close(self, source: str, source_id: str) -> None:
        session = self.sessions.pop((source, source_id), None)
        if session:
            try:
                session.browser.close()
            finally:
                session.playwright.stop()

    def close_all(self) -> None:
        for source, source_id in tuple(self.sessions):
            self.close(source, source_id)
