from __future__ import annotations

from job_search_automation.config import SearchConfig
from job_search_automation.models import Vacancy


def rejection_reason(vacancy: Vacancy, config: SearchConfig) -> str | None:
    if config.remote_only and not vacancy.is_remote():
        return "not remote"
    if config.strict_remote and vacancy.has_non_remote_format():
        return "also offers office, hybrid, or field work"

    searchable = f"{vacancy.title} {vacancy.company} {vacancy.summary}".casefold()
    for keyword in config.excluded_keywords:
        if keyword.casefold() in searchable:
            return f"excluded keyword: {keyword}"
    return None
