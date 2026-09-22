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

On Windows, double-click `run-search.cmd` to scan and open the generated HTML report. The script creates a local `config.toml` from the example if it is missing and restores the matching Chromium build when Playwright has been updated.

Edit `search.queries` in `config.toml` for the roles you need. The included starter configuration searches across Russia but accepts only vacancies explicitly marked as fully remote. Employer areas such as Moscow are allowed because no trip to the office is required.

HeadHunter asks API clients to identify themselves. Set `hh.user_agent` to `JobSearchAutomation/0.1 (YOUR_EMAIL)` in the ignored local `config.toml`. The address is sent only as an HTTP client contact to HeadHunter and is not stored in Git.

Each scan writes new matches to a timestamped Markdown report under `reports/` and stores all accepted vacancy IDs in `data/jobs.db`. Later scans omit already-seen vacancies from the new-results report.

The same scan also creates an HTML report. To open it automatically from the command line:

```powershell
.\.venv\Scripts\job-search.exe scan --open
```

To verify the local configuration and Chromium installation without contacting a vacancy source:

```powershell
.\.venv\Scripts\job-search.exe doctor
```

To inspect the most recently stored vacancies:

```powershell
.\.venv\Scripts\job-search.exe list --limit 20
```

The application only discovers vacancies at this stage. It does not log in to HeadHunter or submit applications.

## Telegram bot

The bot runs on your computer and searches when you press **🔎 Искать вакансии**. It sends new vacancies in batches, each with the original link and a filled reply text. If nothing new appears, it offers **📚 Ранее найденные**. The same link and reply text are available for vacancies in history. The bot does not send applications to employers.

1. Create a bot with [@BotFather](https://t.me/BotFather). Copy its token into the ignored `config.toml` under `[telegram]` as `bot_token`, or set `TELEGRAM_BOT_TOKEN` in your environment. Do not put the token in `config.example.toml`.
2. Copy `profile.example.json` to `profile.json` and replace the three required fields: `name`, `about`, and `contact`. Optional `skills`, `resume_url`, and `portfolio_url` are included only when provided. The Windows launcher creates the local file for you if it is missing. `profile.json` is ignored by Git. The bot uses only these facts; it does not invent experience or tailor claims to a vacancy.
3. Start the bot with `run-telegram-bot.cmd` or `.\.venv\Scripts\job-search.exe bot`. The Windows launcher also checks the required Chromium installation. Keep the bot running while you use it.
4. Open the bot in Telegram and send `/id`. Put the returned number in `allowed_user_ids` in the ignored `config.toml`, for example `allowed_user_ids = [123456789]`. Restart the bot, send `/start`, then use the two buttons.

The Telegram token and candidate profile stay in local ignored files, but the filled reply text is sent to your private Telegram chat when you request vacancies. Only allowed numeric user IDs can search or read history. The bot accepts private chats only. Search settings remain in `[search]` in `config.toml`, including `remote_only` and `strict_remote`.
