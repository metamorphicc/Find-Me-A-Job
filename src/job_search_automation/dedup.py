from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from job_search_automation.models import Vacancy


def canonical_key(vacancy: Vacancy) -> str:
    title = re.sub(r"\W+", "", vacancy.title.casefold())
    company = re.sub(r"\W+", "", vacancy.company.casefold())
    if title and company and company not in {"неуказана", "заказчикflru"}:
        identity = f"{vacancy.kind}|{title}|{company}"
    else:
        parts = urlsplit(vacancy.url)
        identity = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
