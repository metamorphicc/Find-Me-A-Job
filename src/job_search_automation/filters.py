from __future__ import annotations

from job_search_automation.config import SearchConfig
from job_search_automation.models import Vacancy


def rejection_reason(vacancy: Vacancy, config: SearchConfig) -> str | None:
    if vacancy.kind not in config.kinds:
        return "opportunity kind disabled"
    if (
        config.categories
        and not (vacancy.source == "hh" and config.role_ids)
        and not set(vacancy.categories).intersection(config.categories)
    ):
        return "outside selected professional categories"
    if (
        config.role_ids
        and vacancy.source == "hh"
        and not set(vacancy.professional_role_ids).intersection(config.role_ids)
    ):
        return "outside selected HH roles"
    if config.title_keywords and not any(
        keyword.casefold() in vacancy.title.casefold() for keyword in config.title_keywords
    ):
        return "title does not contain a required phrase"
    if config.remote_only and not vacancy.is_remote():
        return "not remote"
    if config.strict_remote and vacancy.has_non_remote_format():
        return "also offers office, hybrid, or field work"
    # Project budgets are not monthly vacancy salaries; never compare the two.
    if vacancy.kind == "job" and (config.salary_required or config.salary_min is not None):
        if vacancy.salary_from is None and vacancy.salary_to is None:
            return "salary not specified"
        if config.salary_min is not None and vacancy.salary_currency != config.salary_currency:
            return "salary currency differs"
        if config.salary_min is not None and (vacancy.salary_to or vacancy.salary_from or 0) < config.salary_min:
            return "salary below minimum"

    searchable = f"{vacancy.title} {vacancy.company} {vacancy.summary}".casefold()
    for keyword in config.excluded_keywords:
        if keyword.casefold() in searchable:
            return f"excluded keyword: {keyword}"
    return None
