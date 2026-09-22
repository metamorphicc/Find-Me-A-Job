# Job Search Automation

Local Python project for automating job discovery, filtering, deduplication, and application review.

The project is intentionally empty at this stage. The initial environment includes Playwright for browser automation, Requests for HTTP integrations, Pytest for tests, and Ruff for linting.

Candidate profiles, resumes, browser sessions, screenshots, and application history are local-only and excluded from Git.

## Development setup

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
```
