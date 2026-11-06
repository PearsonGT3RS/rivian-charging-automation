FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --create-home app \
    && mkdir -p /app /sessions \
    && chown -R app:app /app /sessions

WORKDIR /app

COPY charging_automation/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -f /tmp/requirements.txt

COPY --chown=app:app charging_automation/ ./

USER app
WORKDIR /app

CMD ["python", "-u", "main.py"]
