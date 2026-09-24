"""Provider taxonomy values for the two focused IT search categories."""

import re

CATEGORY_LABELS = {
    "software": "Разработка и тестирование",
    "it_ops": "Системы, сети и техподдержка",
}

# IDs from the HH professional roles directory, not free-text title matches.
HH_ROLES = {
    "software": ("96", "124", "160", "165"),
    "it_ops": ("112", "113", "114", "116", "121"),
}

REMOTIVE_CATEGORIES = {
    "Software Development": "software",
    "Artificial Intelligence": "software",
    "Quality Assurance": "software",
    "Devops": "it_ops",
    "Information Technology": "it_ops",
}

WWR_CATEGORIES = {
    "Full-Stack Programming": "software",
    "Back-End Programming": "software",
    "Front-End Programming": "software",
    "Programming": "software",
    "DevOps and System Administration": "it_ops",
    "DevOps and Sysadmin": "it_ops",
}

# WWR's feed occasionally assigns a programming category to a non-technical role.
TECH_ROLE_TITLE = re.compile(
    r"\b(?:developer|engineer|programmer|programming|"
    r"devops|sre|sysadmin|system administrator|technical support|tech support|"
    r"it support|it operations|security|qa|tester|testing|software|backend|"
    r"back-end|frontend|front-end|full.stack|data scientist|data engineer|"
    r"machine learning|ai engineer|mobile app|web app|platform|infrastructure|cloud)\b",
    re.IGNORECASE,
)
NON_TECH_ROLE_TITLE = re.compile(
    r"\b(?:sales|account executive|accounts payable|accountant|bookkeeper|"
    r"marketing|recruiter|teacher|tutor|instructor|customer success)\b",
    re.IGNORECASE,
)


def technical_role_title(title: str) -> bool:
    return TECH_ROLE_TITLE.search(title) is not None and NON_TECH_ROLE_TITLE.search(title) is None

# Freelancer.com job taxonomy IDs for concrete development and operations skills.
FREELANCER_JOBS = {
    "software": (
        7, 9, 13, 44, 59, 113, 248, 292, 500, 759, 761, 1031,
        1067, 1092, 1265, 1315, 2164, 2376, 2688,
    ),
    "it_ops": (30, 31, 89, 216, 319, 683, 1002, 1541, 1610, 1678),
}


def hh_role_ids(categories: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(role for category in categories for role in HH_ROLES[category]))


def freelancer_job_ids(categories: tuple[str, ...]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(job for category in categories for job in FREELANCER_JOBS[category]))


def freelancer_categories(jobs: object) -> tuple[str, ...]:
    if not isinstance(jobs, list):
        return ()
    ids = {job.get("id") for job in jobs if isinstance(job, dict)}
    return tuple(category for category, values in FREELANCER_JOBS.items() if ids.intersection(values))
