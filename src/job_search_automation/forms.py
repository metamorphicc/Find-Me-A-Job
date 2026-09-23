from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout


class FormProbeError(ValueError):
    """Raised when the destination is not a safely inspectable Tilda form."""


@dataclass(frozen=True, slots=True)
class FormField:
    index: int
    name: str
    kind: str
    label: str
    required: bool


@dataclass(frozen=True, slots=True)
class FormProbe:
    url: str
    form_index: int
    fields: tuple[FormField, ...]
    has_submit: bool
    has_uploadcare: bool
    has_file_input: bool

    def to_dict(self) -> dict:
        return asdict(self)


def validate_form_url(url: str) -> str:
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise FormProbeError("Нужна публичная HTTPS-ссылка на форму")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
            raise FormProbeError("Локальная форма не поддерживается") from None
    else:
        if not address.is_global:
            raise FormProbeError("Локальная форма не поддерживается")
    if len(value) > 2000:
        raise FormProbeError("Ссылка слишком длинная")
    return value


def inspect_tilda(page: Page) -> tuple[FormProbe, ...]:
    try:
        page.locator("form.t-form").first.wait_for(state="attached", timeout=12_000)
    except PlaywrightTimeout as exc:
        raise FormProbeError("На странице не найдена Tilda-форма") from exc
    raw = page.evaluate(
        r"""() => Array.from(document.querySelectorAll('form.t-form')).map((form, formIndex) => {
          const controls = Array.from(form.querySelectorAll('input, textarea, select'));
          const fields = controls.map((input, index) => {
            const group = input.closest('.t-input-group');
            const title = group?.querySelector('.t-input-title, label')?.textContent || '';
            const label = Array.from(input.labels || []).map(item => item.textContent).join(' ') ||
              title || input.getAttribute('placeholder') || '';
            return {
              index,
              name: input.getAttribute('name') || '',
              kind: input.getAttribute('role') === 'uploadcare-uploader' ? 'uploadcare' :
                (input.getAttribute('type') || input.tagName.toLowerCase()).toLowerCase(),
              label: label.trim().replace(/\s+/g, ' '),
              required: input.required || input.dataset.tildaRule === 'req' ||
                input.getAttribute('data-tilda-req') === '1' ||
                input.closest('.t-input-group')?.classList.contains('t-input-group_req') || false
            };
          }).filter(field => !['hidden', 'submit', 'button'].includes(field.kind));
          return {
            url: location.href,
            form_index: formIndex,
            fields,
            has_submit: Boolean(form.querySelector('button[type=submit], input[type=submit], .t-submit')),
            has_uploadcare: fields.some(field => field.kind === 'uploadcare'),
            has_file_input: fields.some(field => field.kind === 'file')
          };
        })"""
    )
    return tuple(
        FormProbe(
            url=validate_form_url(item["url"]),
            form_index=item["form_index"],
            fields=tuple(FormField(**field) for field in item["fields"]),
            has_submit=item["has_submit"],
            has_uploadcare=item["has_uploadcare"],
            has_file_input=item["has_file_input"],
        )
        for item in raw
    )


def select_form(probes: tuple[FormProbe, ...], form_index: int | None = None) -> FormProbe:
    if not probes:
        raise FormProbeError("На странице не найдена Tilda-форма")
    if form_index is None:
        if len(probes) != 1:
            raise FormProbeError("На странице несколько форм; укажите индекс нужной формы")
        selected = probes[0]
    else:
        selected = next((item for item in probes if item.form_index == form_index), None)
        if selected is None:
            raise FormProbeError("Форма с таким индексом не найдена")
    if not selected.has_submit:
        raise FormProbeError("У формы нет явной кнопки отправки")
    return selected
