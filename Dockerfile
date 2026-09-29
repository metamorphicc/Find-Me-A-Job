FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    JOB_SEARCH_HEADLESS=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir . \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 jobbot \
    && mkdir -p /app/runtime/data /app/runtime/reports /app/runtime/artifacts \
        /app/runtime/screenshots /app/runtime/resumes \
    && chown -R jobbot:jobbot /app/runtime /ms-playwright

USER jobbot

CMD ["job-search", "--config", "/app/config.toml", "bot"]
