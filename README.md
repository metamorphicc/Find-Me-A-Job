# Job Search Automation

Local Python application for finding remote vacancies, filtering them, and keeping a private history so the same vacancy is not shown as new twice.

The first provider uses the public [HeadHunter API](https://api.hh.ru/openapi/redoc). Search requests use the current `work_format=REMOTE` filter. If the API is unreachable, the application falls back to the public HH search page through a local headless Chromium browser. Strict remote mode additionally rejects results that also advertise office, hybrid, or field work.

Candidate profiles, resumes, the search database, generated reports, browser sessions, screenshots, and application history are local-only and excluded from Git.

## Development setup

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

## First run

```powershell
Copy-Item .\config.example.toml .\config.toml
notepad .\config.toml
.\.venv\Scripts\job-search.exe scan
```

Edit `search.queries` in `config.toml` for the roles you need. The included starter configuration searches across Russia but accepts only vacancies explicitly marked as fully remote. Employer areas such as Moscow are allowed because no trip to the office is required.

HeadHunter asks API clients to identify themselves. Set `hh.user_agent` to `JobSearchAutomation/0.1 (YOUR_EMAIL)` in the ignored local `config.toml`. The address is sent only as an HTTP client contact to HeadHunter and is not stored in Git.

Each scan writes new matches to a timestamped Markdown report under `reports/` and stores all accepted vacancy IDs in `data/jobs.db`. Later scans omit already-seen vacancies from the new-results report.

To inspect the most recently stored vacancies:

```powershell
.\.venv\Scripts\job-search.exe list --limit 20
```

The application only discovers vacancies at this stage. It does not log in to HeadHunter or submit applications.
