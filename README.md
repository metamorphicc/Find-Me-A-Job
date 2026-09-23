# Job Search Automation

Local Python application for finding remote vacancies, filtering them, and keeping a private history so the same vacancy is not shown as new twice.

The [project roadmap](ROADMAP.md) tracks completed stages, source availability, and integration work that still needs external access.

The app searches HeadHunter, Remotive, We Work Remotely and Freelancer.com, with optional SuperJob and FL.ru adapters. Configure sources under `search.sources` in `config.toml`. HeadHunter uses its [public API](https://api.hh.ru/openapi/redoc) and can fall back to a local headless Chromium browser. Remotive uses its [public API](https://github.com/remotive-io/remote-jobs-api); We Work Remotely and FL.ru use their RSS feeds. Freelancer.com uses its public active-project API to find freelance work; it does not place bids. SuperJob requires an application key in the `SUPERJOB_APP_KEY` environment variable. A failed source is reported while other sources continue. The FL.ru feed currently returns HTTP 403 from some networks, so leave it disabled until it works from yours.

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

Edit `search.queries` and `search.sources` in `config.toml` for the roles and feeds you need. The included starter configuration searches HH in Russia plus worldwide remote listings. HH's `area_ids` and `experience_ids` apply only to HH; other sources are matched locally against search phrases. Every international listing shows its stated location scope. Check whether the employer can hire from your country before applying: remote does not mean worldwide.

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

To inspect a public employer form without filling or submitting it, run:

```powershell
.\.venv\Scripts\job-search.exe form-probe https://example.com/application
```

The command reports the form's fields and whether it has a submit control and file upload. It handles Tilda and simple standard HTML forms. If a page has several forms, pass `--form-index N` after identifying the correct one. The form URL must use public HTTPS.

In the bot, open an opportunity card and press **📄 Подготовить заявку**. Press **🔎 Найти форму** to inspect its original page for an explicit application link, or send the employer's public HTTPS form URL yourself. The bot opens a visible browser on the same computer, fills known text fields from the chosen template and profile, attaches the local résumé when there is a file input, and leaves unknown required fields, selections and consents for you. Uploadcare transfers the résumé to the employer form's configured account during Tilda preparation. Review the form in the browser; the bot records a screenshot under ignored `screenshots/` and a value-free review summary under ignored `artifacts/`. After manual corrections, press **🔄 Проверить снова**. When no required fields are missing, **✅ Отправить эту заявку** approves that exact reviewed form and clicks its final submit control once. The bot marks success only when a supported on-page confirmation appears. An unclear result blocks another attempt until you check it manually. The bot must stay running to keep the browser session alive. Login, OTP, CAPTCHA and complex custom application widgets still require manual work.

## Telegram bot

The bot runs on your computer and searches when you press **🔎 Искать вакансии**. It sends new vacancies in batches, each with the original link. If nothing new appears, it offers **📚 Ранее найденные**. Search and history work without a candidate profile. If you add one, both new and historical vacancies also include a filled reply text. Sending a supported Tilda application requires a separate explicit confirmation after reviewing its filled form.

Use **⚙️ Настройки** (or `/settings`) in the private bot chat to change search phrases, remote-only rules, experience, region, vacancy age, result limit, and excluded words. The same menu lets you edit the name, introduction, contact, skills, résumé link, and portfolio link used in reply text. After choosing a field, send its new value as a message; `/cancel` leaves it unchanged. A dash (`-`) clears optional fields or excluded words. Profile details are optional for searching.

The **Источники** menu enables or disables HH, Remotive, We Work Remotely, Freelancer.com, SuperJob and FL.ru. The **Работа / заказы** menu lets you search vacancies, freelance work, or both. Cross-source matches with the same company and title appear once in new results and history; the underlying source records are retained. International opportunities display their stated candidate location rather than assuming every remote role accepts applicants from everywhere.

Bot edits to search filters are saved in ignored `data/search-settings.json`. These values override `[search]` in `config.toml` for both bot and CLI scans until that local settings file is removed. Profile edits are saved in ignored `profile.json`. Changes take effect without restarting the bot. Previously discovered vacancies stay in history when filters change. The optional email, phone and city fields can help fill external forms. To prepare a file upload, set `resume_path` in local `profile.json` to a PDF, DOC or DOCX file (for example, `resumes/cv.pdf`); the file stays outside Git.

Use **⚙️ Настройки → ✉️ Шаблоны отклика** to edit the general, internship, Python, analytics, RU freelance, international freelance, and international job replies. International/freelance templates are chosen by opportunity type and market; RU jobs still use title keywords and then the general template. Each template can also hold explicit values for particular form fields (`Название поля = значение`). The edited templates stay in ignored `data/reply-templates.json`; older four-template files are loaded with the new defaults added. Each card has **📝 Другой шаблон** to preview a different reply.

Template text supports `{name}`, `{title}`, `{company}`, `{about}`, `{about_en}`, `{skills_line}`, `{resume_line}`, `{portfolio_line}`, `{contact_line}`, `{experience_line}`, `{education_line}`, `{languages_line}`, `{availability_line}`, and `{rate_line}`. The bot profile also holds experience, education, languages, timezone, availability, work authorization, rate, an English introduction, résumé file path, and up to 30 named custom facts. For Tilda text fields, a matching value from the selected template takes priority; otherwise the bot uses a known profile field or an exactly named custom fact. Unknown fields and consents remain for manual review. The review shows which source filled each field. Do not put claims you cannot support in a template or profile. Searching and showing reply text never sends an application.

Before publishing, run `.\.venv\Scripts\python.exe scripts/privacy_scan.py .`. It checks tracked files for local candidate data paths and common credentials.

1. Create a bot with [@BotFather](https://t.me/BotFather). Copy its token into the ignored `config.toml` under `[telegram]` as `bot_token`, or set `TELEGRAM_BOT_TOKEN` in your environment. Do not put the token in `config.example.toml`.
2. Start the bot with `run-telegram-bot.cmd` or `.\.venv\Scripts\job-search.exe bot`. The Windows launcher also checks the required Chromium installation. Keep the bot running while you use it.
3. Optionally use **⚙️ Настройки → Данные для отклика** or copy `profile.example.json` to `profile.json` and replace `name`, `about`, and `contact` to add ready-to-copy reply text. Optional `skills`, `resume_url`, and `portfolio_url` are included only when provided. `profile.json` is ignored by Git. The bot uses only these facts; it does not invent experience or tailor claims to a vacancy.
4. Open the bot in Telegram and send `/id`. Put the returned number in `allowed_user_ids` in the ignored `config.toml`, for example `allowed_user_ids = [123456789]`. Restart the bot, send `/start`, then use the two buttons.

The Telegram token and candidate profile stay in local ignored files, but values you edit through the bot and filled reply text are sent through your private Telegram chat. Only allowed numeric user IDs can search, read history, or edit settings. The bot accepts private chats only.

For automatic morning searches, enable **⚙️ Настройки → ⏰ Ежедневный поиск** and set the time and IANA time zone. The bot checks once per local day and remembers the last run across restarts. It must stay running; to search while your computer is off, use the [Linux VPS service guide](deploy/README.md). On a headless server, the bot sends a screenshot of a prepared form to your private chat before offering submission.
