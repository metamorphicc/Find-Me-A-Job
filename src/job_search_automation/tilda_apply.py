from __future__ import annotations

import mimetypes
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from job_search_automation.forms import FormField, FormProbe
from job_search_automation.models import Vacancy
from job_search_automation.profile import CandidateProfile, ProfileError, resume_file
from job_search_automation.reply_templates import ReplyTemplate, render_reply


class FillError(ValueError):
    """Raised when a field or upload cannot be prepared safely."""


@dataclass(frozen=True, slots=True)
class FillResult:
    filled: tuple[str, ...]
    missing_required: tuple[str, ...]
    uploaded_filename: str | None


def _field_value(field: FormField, profile: CandidateProfile, reply: str) -> str | None:
    label = f"{field.name} {field.label}".casefold().replace("ё", "е")
    name = field.name.casefold()
    if field.kind == "email" or any(word in label for word in ("email", "e-mail", "почт")):
        return profile.email or None
    if field.kind == "tel" or any(word in label for word in ("телефон", "phone", "mobile")):
        return profile.phone or None
    if name in {"name", "fullname", "full_name", "fio"} or any(
        word in label for word in ("ваше имя", "фио", "full name", "your name")
    ):
        return profile.name
    if any(word in label for word in ("город", "city", "место проживания")):
        return profile.city or None
    if any(
        word in label
        for word in ("сопровод", "письмо", "cover letter", "message", "сообщен", "комментар")
    ):
        return reply
    if any(word in label for word in ("о себе", "about you")):
        return profile.about
    if any(word in label for word in ("портфолио", "portfolio")):
        return profile.portfolio_url or None
    if any(word in label for word in ("резюме", "resume", "cv")):
        return profile.resume_url or None
    return None


def _fill_text(locator: Locator, value: str) -> None:
    try:
        locator.fill(value, timeout=5_000)
        locator.dispatch_event("blur")
        if locator.input_value() == value:
            return
    except PlaywrightError:
        pass
    locator.evaluate(
        """(el, value) => {
          el.value = value;
          for (const type of ['keydown', 'input', 'keyup', 'change', 'blur']) {
            el.dispatchEvent(new Event(type, {bubbles: true}));
          }
        }""",
        value,
    )
    if locator.input_value() != value:
        raise FillError("Поле не приняло значение")


def uploadcare_attach(page: Page, locator: Locator, path: Path) -> None:
    public_key = locator.get_attribute("data-public-key")
    if not public_key:
        raise FillError("У Uploadcare-поля нет публичного ключа")
    try:
        page.wait_for_function("typeof window.uploadcare !== 'undefined'", timeout=10_000)
    except PlaywrightError as exc:
        raise FillError("Uploadcare не загрузился на странице") from exc
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        with path.open("rb") as stream:
            response = requests.post(
                "https://upload.uploadcare.com/base/",
                data={"UPLOADCARE_PUB_KEY": public_key, "UPLOADCARE_STORE": "1"},
                files={"file": (path.name, stream, content_type)},
                timeout=90,
            )
        response.raise_for_status()
        uuid = response.json().get("file")
    except (requests.RequestException, ValueError, OSError) as exc:
        raise FillError("Не удалось загрузить резюме в Uploadcare") from exc
    if not isinstance(uuid, str) or not re.fullmatch(r"[a-fA-F0-9-]{36}", uuid):
        raise FillError("Uploadcare не подтвердил загрузку файла")
    result = locator.evaluate(
        """async (input, {uuid, publicKey, filename}) => {
          const widget = window.uploadcare.Widget(input);
          const file = window.uploadcare.fileFrom('uploaded', uuid, {publicKey});
          widget.value(file);
          try { await file.done(); } catch (_) { return {ok: false}; }
          const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
          for (let tries = 0; tries < 50; tries++) {
            const holder = input.closest('.t-uploadcare') || input.parentElement;
            const status = holder?.querySelector('.uploadcare--widget')?.getAttribute('data-status');
            const text = holder?.querySelector('.uploadcare--widget__text')?.textContent || '';
            const value = input.value || '';
            if (status === 'loaded' && text.includes(filename) && value.includes(uuid)) {
              return {ok: true, value, status, text};
            }
            await sleep(400);
          }
          return {ok: false};
        }""",
        {"uuid": uuid, "publicKey": public_key, "filename": path.name},
    )
    value = result.get("value", "")
    host = urlparse(value).hostname or ""
    if not result.get("ok") or host != "ucarecdn.com":
        raise FillError("Uploadcare не показал прикреплённый файл и ссылку CDN")


def fill_tilda(
    page: Page,
    probe: FormProbe,
    profile: CandidateProfile,
    profile_path: Path,
    vacancy: Vacancy,
    template: ReplyTemplate,
    *,
    uploader: Callable[[Page, Locator, Path], None] = uploadcare_attach,
) -> FillResult:
    form = page.locator("form.t-form").nth(probe.form_index)
    if form.count() != 1 or page.url != probe.url:
        raise FillError("Форма изменилась после проверки")
    reply = render_reply(profile, vacancy, template)
    filled: list[str] = []
    missing: list[str] = []
    uploaded_filename: str | None = None
    for field in probe.fields:
        locator = form.locator("input, textarea, select").nth(field.index)
        label = field.label or field.name or f"поле {field.index + 1}"
        if field.kind in {"file", "uploadcare"}:
            try:
                path = resume_file(profile, profile_path)
            except ProfileError:
                if field.required:
                    missing.append(label)
                continue
            if field.kind == "file":
                locator.set_input_files(str(path))
                if path.name not in locator.evaluate("el => Array.from(el.files).map(f => f.name)"):
                    raise FillError("Резюме не прикрепилось к форме")
            else:
                uploader(page, locator, path)
            uploaded_filename = path.name
            filled.append(label)
            continue
        if field.kind in {"checkbox", "radio", "select", "select-one"}:
            if field.required:
                missing.append(label)
            continue
        value = _field_value(field, profile, reply)
        if value:
            _fill_text(locator, value)
            filled.append(label)
        elif field.required:
            missing.append(label)
    return FillResult(tuple(filled), tuple(missing), uploaded_filename)
