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

The scan command discovers vacancies. It does not log in to HeadHunter or submit applications.

To inspect an employer's Tilda form without filling or submitting it, run:

```powershell
.\.venv\Scripts\job-search.exe form-probe https://example.com/application
```

The command reports the form's fields and whether it has a submit control and file upload. If a page has several Tilda forms, pass `--form-index N` after identifying the correct one. The form URL must use public HTTPS.

## Telegram bot

The bot runs on your computer and searches when you press **🔎 Искать вакансии**. It sends new vacancies in batches, each with the original link. If nothing new appears, it offers **📚 Ранее найденные**. Search and history work without a candidate profile. If you add one, both new and historical vacancies also include a filled reply text. The bot does not send applications to employers.

Use **⚙️ Настройки** (or `/settings`) in the private bot chat to change search phrases, remote-only rules, experience, region, vacancy age, result limit, and excluded words. The same menu lets you edit the name, introduction, contact, skills, résumé link, and portfolio link used in reply text. After choosing a field, send its new value as a message; `/cancel` leaves it unchanged. A dash (`-`) clears optional fields or excluded words. Profile details are optional for searching.

Bot edits to search filters are saved in ignored `data/search-settings.json`. These values override `[search]` in `config.toml` for both bot and CLI scans until that local settings file is removed. Profile edits are saved in ignored `profile.json`. Changes take effect without restarting the bot. Previously discovered vacancies stay in history when filters change. The optional email, phone and city fields can help fill external forms. To prepare a file upload, set `resume_path` in local `profile.json` to a PDF, DOC or DOCX file (for example, `resumes/cv.pdf`); the file stays outside Git.

Use **⚙️ Настройки → ✉️ Шаблоны отклика** to edit the general, internship, Python, and analytics replies. Initially they share the same neutral wording. For each template, you can replace its text; for role-specific templates, you can also change the words that select it. The bot compares those words with the vacancy title, checking internship, Python, and analytics in that order, then uses the general template if none match. The edited templates stay in ignored `data/reply-templates.json`. Existing vacancies use the latest template text when you reopen them. Each vacancy card also has **📝 Другой шаблон** to preview a different reply for that vacancy; this does not change the automatic rule.

Template text supports `{name}`, `{title}`, `{company}`, `{about}`, `{skills_line}`, `{resume_line}`, `{portfolio_line}`, and `{contact_line}`. The values come only from your profile and the vacancy. Keep role-specific claims in your own profile or template text factual. Optional skills and links disappear when their profile fields are empty. Invalid placeholders are rejected when saving. The bot never submits the reply for you.

1. Create a bot with [@BotFather](https://t.me/BotFather). Copy its token into the ignored `config.toml` under `[telegram]` as `bot_token`, or set `TELEGRAM_BOT_TOKEN` in your environment. Do not put the token in `config.example.toml`.
2. Start the bot with `run-telegram-bot.cmd` or `.\.venv\Scripts\job-search.exe bot`. The Windows launcher also checks the required Chromium installation. Keep the bot running while you use it.
3. Optionally use **⚙️ Настройки → Данные для отклика** or copy `profile.example.json` to `profile.json` and replace `name`, `about`, and `contact` to add ready-to-copy reply text. Optional `skills`, `resume_url`, and `portfolio_url` are included only when provided. `profile.json` is ignored by Git. The bot uses only these facts; it does not invent experience or tailor claims to a vacancy.
4. Open the bot in Telegram and send `/id`. Put the returned number in `allowed_user_ids` in the ignored `config.toml`, for example `allowed_user_ids = [123456789]`. Restart the bot, send `/start`, then use the two buttons.

The Telegram token and candidate profile stay in local ignored files, but values you edit through the bot and filled reply text are sent through your private Telegram chat. Only allowed numeric user IDs can search, read history, or edit settings. The bot accepts private chats only.
