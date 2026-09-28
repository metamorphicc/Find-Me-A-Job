# Job Search Automation

Local Python application for finding remote vacancies, filtering them, and keeping a private history so the same vacancy is not shown as new twice.

The [project roadmap](ROADMAP.md) tracks completed stages, source availability, and integration work that still needs external access.

The app searches HeadHunter, Remotive and We Work Remotely for vacancies, with optional [Remote OK](https://remoteok.com/faq) and [Jobicy](https://jobicy.com/jobs-rss-feed) public feeds. It searches Freelancer.com and Freelancehunt for freelance projects, with optional SuperJob and FL.ru adapters. Configure vacancy sources under `search.sources` in `config.toml`; the bot keeps separate local order sources. HeadHunter uses its [public API](https://api.hh.ru/openapi/redoc) and can fall back to a local headless Chromium browser. Remotive uses its [public API](https://github.com/remotive-io/remote-jobs-api); We Work Remotely and FL.ru use their RSS feeds. Freelancer.com uses its public active-project API; Freelancehunt uses its [open-project API](https://apidocs.freelancehunt.com/). Neither adapter places bids. SuperJob requires an application key in the `SUPERJOB_APP_KEY` environment variable. A failed source is reported while other sources continue. The FL.ru feed intermittently returns HTTP 403 from this network, so it remains disabled in the bot.

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

Edit `search.sources` and `search.categories` in `config.toml` for the vacancy feeds and professions you need. By default, the vacancy section searches **software development**, checks for a matching development phrase in the title and a matching technology in the title or summary, and excludes QA, DevOps and support titles. The HH software category uses the developer role; other HH roles remain selectable. In Telegram, **🔎 Искать вакансии → ⚙️ Фильтры вакансий** lets you edit title phrases, stack terms and excluded titles, select exact HH professional roles, HH employment forms, work schedules, experience and area, and set a minimum salary and currency. Exact HH roles override broad HH categories. A stack term must appear in the available title or summary, so terse listings may be hidden. A salary threshold hides vacancies without a numeric salary. Freelance project budgets are not compared with monthly salaries. Set `categories = []` to return to the older `search.queries` text mode. HH's `area_ids`, `experience_ids`, `role_ids`, `employment_forms` and `work_schedules` apply only to HH. Every international listing shows its stated location scope. Check whether the employer can hire from your country before applying: remote does not mean worldwide.

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

Create a private profile skeleton without adding any invented personal details:

```powershell
.\.venv\Scripts\job-search.exe profile --init
```

The command reports which facts are still needed for ready-to-copy replies, without printing profile values. In Telegram, open the separate **👤 Профиль** button in the main menu (or send `/profile`). It has sections for basic facts, skills and experience, links and résumé, work preferences, and custom facts. Use **📋 Заполнить основу** to enter your name, factual introduction and contact one at a time; the other fields can be edited individually. The ignored local `profile.json` is never committed; do not put personal data in `profile.example.json`.

To inspect a public employer form without filling or submitting it, run:

```powershell
.\.venv\Scripts\job-search.exe form-probe https://example.com/application
```

The command reports the form's fields and whether it has a submit control and file upload. It handles Tilda and simple standard HTML forms. If a page has several forms, pass `--form-index N` after identifying the correct one. The form URL must use public HTTPS.

In the bot, open an opportunity card and press **📄 Подготовить заявку**. Press **🔎 Найти форму** to inspect its original page for an explicit application link, or send the employer's public HTTPS form URL yourself. The bot opens a visible browser on the same computer, fills known text fields from the chosen template and profile, attaches the local résumé when there is a file input, and leaves unknown required fields, selections and consents for you. Uploadcare transfers the résumé to the employer form's configured account during Tilda preparation. Review the form in the browser; the bot records a screenshot under ignored `screenshots/` and a value-free review summary under ignored `artifacts/`. After manual corrections, press **🔄 Проверить снова**. When no required fields are missing, **✅ Отправить эту заявку** approves that exact reviewed form and clicks its final submit control once. The bot marks success only when a supported on-page confirmation appears. An unclear result blocks another attempt until you check it manually. The bot must stay running to keep the browser session alive. Login, OTP, CAPTCHA and complex custom application widgets still require manual work.

## Telegram bot

The bot runs on your computer. Its main menu has separate **🔎 Искать вакансии** and **🧩 Искать заказы** sections. Each section has its own search button, history and filters. Cards have arrow navigation in one Telegram message and keep the original link. Search and history work without a candidate profile. With a completed profile, press **📝 Показать отклик** on a new or historical card to open its filled reply in a separate message. Sending a supported Tilda application requires a separate explicit confirmation after reviewing its filled form.

Use the vacancy section to change professional categories, job sources, remote-only rules, experience, region, vacancy age, result limit, stack terms and excluded words or titles. Text search phrases are available when you switch off the category filter. The order section keeps independent settings under **⚙️ Фильтры заказов** in ignored `data/freelance-search-settings.json`: sources, technical topics, excluded words, age and result limit. It searches software projects from Freelancer.com and Freelancehunt by default. The topic must appear in the title or description; skill tags can also confirm a project whose title explicitly asks for implementation. Design, marketing, mobile and unrelated operations titles are excluded by default. It does not require "developer" in the project title and does not filter by the client's country; check whether you can bid from your location on each platform. On-site local projects are excluded where the source identifies them. The FL.ru adapter is not enabled here because its RSS access and technical categories are not reliable. Use the separate **👤 Профиль** section to edit the facts used in reply text. After choosing a field, send its new value as a message; `/cancel` leaves it unchanged. A dash (`-`) clears optional exclusions. Profile details are optional for searching.

The vacancy **Источники** menu enables or disables HH, Remotive, We Work Remotely, Remote OK, Jobicy and SuperJob. Remote OK and Jobicy remain off until selected there; their cards link back to the original boards and display the source name. The order **Источники заказов** menu enables or disables Freelancer.com and Freelancehunt independently of older saved vacancy settings. Job and order cards have separate histories while sharing the underlying database. Cross-source matches with the same company and title appear once; source records are retained. International opportunities show the available location information rather than assuming every remote role accepts applicants from everywhere.

Category mode uses HH professional-role IDs and Remotive/We Work Remotely category fields for vacancies, and Freelancer.com/Freelancehunt project skill IDs for orders. We Work Remotely occasionally labels nontechnical roles as programming, so its cards also require a technical role title. SuperJob does not yet have a verified category adapter; if enabled in this mode, it reports that limitation instead of adding unrelated results. Each history view follows its own current filters; old unrelated cards remain stored but are hidden until the filter changes.

Bot edits to search filters are saved in ignored `data/search-settings.json`. These values override `[search]` in `config.toml` for both bot and CLI scans until that local settings file is removed. Profile edits are saved in ignored `profile.json`. Changes take effect without restarting the bot. Previously discovered vacancies stay in history when filters change. The optional email, phone and city fields can help fill external forms. To prepare a file upload, set `resume_path` in local `profile.json` to a PDF, DOC or DOCX file (for example, `resumes/cv.pdf`); the file stays outside Git.

Use **⚙️ Настройки → ✉️ Шаблоны отклика** to edit the general, internship, Python, analytics, RU freelance, international freelance, and international job replies. International/freelance templates are chosen by opportunity type and market; RU jobs still use title keywords and then the general template. Each template can also hold explicit values for particular form fields (`Название поля = значение`). The edited templates stay in ignored `data/reply-templates.json`; older four-template files are loaded with the new defaults added. Each card has **🔁 Другой шаблон** to preview an alternative reply.

Template text supports `{name}`, `{title}`, `{company}`, `{about}`, `{about_en}`, `{skills_line}`, `{resume_line}`, `{portfolio_line}`, `{contact_line}`, `{experience_line}`, `{education_line}`, `{languages_line}`, `{availability_line}`, and `{rate_line}`. The bot profile also holds experience, education, languages, timezone, availability, work authorization, rate, an English introduction, résumé file path, and up to 30 named custom facts. For Tilda text fields, a matching value from the selected template takes priority; otherwise the bot uses a known profile field or an exactly named custom fact. Unknown fields and consents remain for manual review. The review shows which source filled each field. Do not put claims you cannot support in a template or profile. Searching and showing reply text never sends an application.

Before publishing, run `.\.venv\Scripts\python.exe scripts/privacy_scan.py .`. It checks tracked files for local candidate data paths and common credentials.

1. Create a bot with [@BotFather](https://t.me/BotFather). Copy its token into the ignored `config.toml` under `[telegram]` as `bot_token`, or set `TELEGRAM_BOT_TOKEN` in your environment. Do not put the token in `config.example.toml`.
2. Start the bot with `run-telegram-bot.cmd` or `.\.venv\Scripts\job-search.exe bot`. The Windows launcher also checks the required Chromium installation. Keep the bot running while you use it.
3. Optionally use **⚙️ Настройки → Данные для отклика** or copy `profile.example.json` to `profile.json` and replace `name`, `about`, and `contact` to add ready-to-copy reply text. Optional `skills`, `resume_url`, and `portfolio_url` are included only when provided. `profile.json` is ignored by Git. The bot uses only these facts; it does not invent experience or tailor claims to a vacancy.
4. Open the bot in Telegram and send `/id`. Put the returned number in `allowed_user_ids` in the ignored `config.toml`, for example `allowed_user_ids = [123456789]`. Restart the bot, send `/start`, then use the two buttons.

The Telegram token and candidate profile stay in local ignored files, but values you edit through the bot and filled reply text are sent through your private Telegram chat. Only allowed numeric user IDs can search, read history, or edit settings. The bot accepts private chats only.

For automatic morning vacancy searches, enable **⚙️ Настройки → ⏰ Ежедневный поиск** and set the time and IANA time zone. This existing schedule currently runs the vacancy section only; order searches start from their own menu button. The bot checks once per local day and remembers the last run across restarts. It must stay running; to search while your computer is off, use the [Linux VPS service guide](deploy/README.md). On a headless server, the bot sends a screenshot of a prepared form to your private chat before offering submission.
