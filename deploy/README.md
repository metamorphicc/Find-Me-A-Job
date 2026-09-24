# Always-on Telegram bot on a Linux VPS

Run one bot instance per token. Stop the Windows bot before starting this service, or Telegram will reject one of the long-polling clients.

1. Create an unprivileged `jobbot` user and put this repository at `/opt/find-me-a-job`, owned by that user. Do not commit or publish `config.toml`, `profile.json`, `.env`, résumés, the `data/` directory, or browser screenshots.
2. As `jobbot`, create `.venv`, install the package, and install Chromium:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -e .
   .venv/bin/python -m playwright install chromium
   ```

   Install Chromium's Linux packages once as an administrator with `.venv/bin/python -m playwright install-deps chromium`. Playwright's [official browser guide](https://playwright.dev/python/docs/browsers) documents this command.

3. Copy `config.example.toml` to `config.toml` and set your search phrases, `telegram.allowed_user_ids`, and `[schedule]`. Fill `profile.json` only with facts you want to use in replies. Put `TELEGRAM_BOT_TOKEN=...` in an untracked `.env` file readable only by `jobbot` (`chmod 600 .env`). `SUPERJOB_APP_KEY=...` is optional.
4. Copy `deploy/job-search-bot.service` to `/etc/systemd/system/`, then run `sudo systemctl daemon-reload` and `sudo systemctl enable --now job-search-bot`. Check `sudo systemctl status job-search-bot` and `sudo journalctl -u job-search-bot -f`.

The service restarts after failures and starts after reboot. Scheduled searches run at the configured IANA time zone while the service is active. The bot also lets you change the time and toggle daily search in **Настройки → Ежедневный поиск**; those edits are stored under ignored `data/` and do not need a restart.

With `JOB_SEARCH_HEADLESS=1`, prepared applications open in a headless browser. The bot sends a screenshot to your private chat before showing the submit button. Unknown required fields cannot be corrected in that hidden browser. Add the missing fact or a template field value, prepare the form again, and review the new screenshot; or stop the VPS bot and use the visible local bot for manual corrections. A failed screenshot upload blocks the submit button. OTP, CAPTCHA, login flows, and custom widgets remain manual.
