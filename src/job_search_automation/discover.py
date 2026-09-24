from __future__ import annotations

import re
from collections.abc import Callable

from playwright.sync_api import Page, sync_playwright

from job_search_automation.forms import FormProbeError, inspect_forms, validate_form_url


def discover_application_links(
    url: str, *, configure_page: Callable[[Page], None] | None = None
) -> tuple[str, ...]:
    """Return explicit public application links; never click or submit them."""
    target = validate_form_url(url)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            if configure_page:
                configure_page(page)
            page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            try:
                if inspect_forms(page):
                    return (validate_form_url(page.url),)
            except FormProbeError:
                pass
            raw = page.locator("a[href]").evaluate_all(
                """links => links.map(link => ({
                  url: link.href,
                  label: [link.innerText, link.getAttribute('aria-label'),
                    link.getAttribute('title')].filter(Boolean).join(' ')
                }))"""
            )
        finally:
            browser.close()
    candidates = []
    for item in raw:
        link = str(item.get("url") or "")
        label = str(item.get("label") or "")
        if not re.search(r"apply|application|отклик|подать заявку", label + " " + link, re.IGNORECASE):
            continue
        if re.search(r"login|signin|sign-in|register|vacancy_response", link, re.IGNORECASE):
            continue
        try:
            link = validate_form_url(link)
        except FormProbeError:
            continue
        if link not in candidates:
            candidates.append(link)
    return tuple(candidates[:5])
