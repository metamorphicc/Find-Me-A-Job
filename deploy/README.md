# Запуск Telegram-бота на VPS

Заготовка рассчитана на Linux-сервер с Docker Engine и Compose plugin. После первой настройки бот запускается одной командой, переживает перезагрузку сервера и хранит состояние в Docker volume. Открывать входящие порты для Telegram не нужно: бот использует long polling.

## Первая настройка

1. Установите [Docker Engine и Compose plugin](https://docs.docker.com/engine/install/) на сервере и скопируйте на него репозиторий с этой версией кода. Перейдите в корень проекта.
2. Создайте локальные файлы, которые не попадают в Git и Docker-образ:

   ```bash
   cp deploy/config.vps.example.toml config.toml
   mkdir -p secrets resumes
   chmod 700 secrets
   nano secrets/telegram_bot_token
   chmod 600 secrets/telegram_bot_token
   nano config.toml
   ```

   В файл `secrets/telegram_bot_token` вставьте **только токен** от BotFather. В `config.toml` замените `YOUR_EMAIL` на контакт для HH API и `123456789` на свой Telegram ID. Если ID неизвестен, сначала запустите бота с примером, отправьте ему `/id`, затем исправьте `allowed_user_ids` и перезапустите контейнер. Токен в `config.toml` не нужен.

3. До постоянного запуска проверьте конфигурацию и Chromium:

   ```bash
   docker compose build
   docker compose run --rm bot job-search --config /app/config.toml doctor
   ```

4. Остановите локальный Windows-бот с тем же токеном и запустите серверного:

   ```bash
   docker compose up -d
   docker compose ps
   docker compose logs --tail=50 bot
   ```

Далее обычный запуск после настройки — `docker compose up -d`. После обновления кода — `git pull` и `docker compose up -d --build`. Для остановки — `docker compose down`; база, профиль, шаблоны, состояние расписания и снимки форм остаются в volume `runtime`.

## Где находятся данные

- `config.toml` на сервере монтируется только для чтения. Изменили токен или конфигурацию — выполните `docker compose up -d --force-recreate`.
- `secrets/telegram_bot_token` монтируется как Docker Compose secret. Не отправляйте этот файл в Git и не показывайте его в логах или скриншотах.
- Docker volume `runtime` хранит SQLite, сохранённые фильтры, профиль, шаблоны, расписание, отчёты и снимки форм. Изменения профиля и фильтров через Telegram сохраняются там и не требуют пересборки.
- Если нужно приложить резюме к поддерживаемой форме, положите PDF/DOC/DOCX на сервер в `resumes/`, например `resumes/cv.pdf`. В поле профиля `resume_path` укажите `resumes/cv.pdf`. Эта папка видна контейнеру только для чтения.

Команда `docker compose logs -f --tail=50 bot` показывает ошибки и ход работы. Если Telegram сообщает о конфликте long polling, проверьте, что с тем же токеном не запущен другой экземпляр бота. Серверная среда работает без видимого окна Chromium: перед отправкой поддерживаемой анкеты бот присылает снимок заполненной формы. Неизвестные поля, CAPTCHA, OTP и вход в аккаунт остаются ручными.

Ежедневное расписание сейчас запускает **поиск вакансий**. Поиск заказов запускается кнопкой в Telegram; постоянная работа контейнера сама по себе не добавляет ежедневный поиск заказов.

Альтернативный запуск без Docker через `deploy/job-search-bot.service` остаётся возможным, но для него нужно вручную подготовить Python, Chromium и systemd-службу.
