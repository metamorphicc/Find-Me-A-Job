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
    field_sources: tuple[str, ...] = ()


def _fact_key(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold().replace("ё", "е"))


def _field_value(
    field: FormField, profile: CandidateProfile, reply: str, template: ReplyTemplate
) -> tuple[str | None, str]:
    label = f"{field.name} {field.label}".casefold().replace("ё", "е")
    name = field.name.casefold()
    keys = {_fact_key(field.name), _fact_key(field.label)} - {""}
    for key, value in template.form_values:
        if _fact_key(key) in keys:
            return value, "шаблон"
    if field.kind == "email" or any(word in label for word in ("email", "e-mail", "почт")):
        return profile.email or None, "профиль"
    if field.kind == "tel" or any(word in label for word in ("телефон", "phone", "mobile")):
        return profile.phone or None, "профиль"
    if name in {"name", "fullname", "full_name", "fio"} or any(
        word in label for word in ("ваше имя", "фио", "full name", "your name")
    ):
        return profile.name, "профиль"
    if any(word in label for word in ("город", "city", "место проживания")):
        return profile.city or None, "профиль"
    if any(
        word in label
        for word in ("сопровод", "письмо", "cover letter", "message", "сообщен", "комментар")
    ):
        return reply, "шаблон + профиль"
    if any(word in label for word in ("о себе", "about you")):
        return profile.about_en if "about you" in label and profile.about_en else profile.about, "профиль"
    if any(word in label for word in ("портфолио", "portfolio")):
        return profile.portfolio_url or None, "профиль"
    if any(word in label for word in ("резюме", "resume", "cv")):
        return profile.resume_url or None, "профиль"
    standard = (
        (("опыт", "experience"), profile.experience),
        (("образован", "education"), profile.education),
        (("язык", "languages"), profile.languages),
        (("часовой пояс", "timezone", "time zone"), profile.timezone),
        (("доступность", "availability", "start date"), profile.availability),
        (("право на работу", "work authorization", "work permit"), profile.work_authorization),
        (("ставка", "rate", "salary expectation"), profile.rate),
    )
    for patterns, value in standard:
        if value and any(pattern in label for pattern in patterns):
            return value, "профиль"
    for key, value in profile.facts:
        if _fact_key(key) in keys:
            return value, "профиль: доп. факт"
    return None, ""


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
    selector = "form.t-form" if probe.family == "tilda" else "form"
    form = page.locator(selector).nth(probe.form_index)
    if form.count() != 1 or page.url != probe.url:
        raise FillError("Форма изменилась после проверки")
    reply = render_reply(profile, vacancy, template)
    filled: list[str] = []
    field_sources: list[str] = []
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
            field_sources.append(f"{label}: резюме из профиля")
            continue
        if field.kind in {"checkbox", "radio", "select", "select-one"}:
            if field.required:
                missing.append(label)
            continue
        value, origin = _field_value(field, profile, reply, template)
        if value:
            _fill_text(locator, value)
            filled.append(label)
            field_sources.append(f"{label}: {origin}")
        elif field.required:
            missing.append(label)
    return FillResult(tuple(filled), tuple(missing), uploaded_filename, tuple(field_sources))
