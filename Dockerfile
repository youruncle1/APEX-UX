FROM python:3.11-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir playwright && \
    playwright install chromium firefox webkit && \
    playwright install-deps chromium firefox webkit && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /root/.cache/ms-playwright/

COPY . .

RUN chmod +x scripts/entrypoint.sh

ARG APP_PORT=8002
ENV APP_PORT=${APP_PORT}

CMD ["scripts/entrypoint.sh"]