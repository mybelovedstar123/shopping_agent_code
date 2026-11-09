FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY kyo ./kyo
RUN pip install --no-cache-dir .

# Run as an unprivileged user, with one writable folder for the database.
RUN useradd --create-home --uid 1000 kyo && mkdir /data && chown kyo /data
USER kyo

ENV KYO_DATABASE_URL=sqlite:////data/kyo_shop.sqlite3 \
    KYO_HOST=0.0.0.0 \
    KYO_PORT=8000

EXPOSE 8000
VOLUME /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://127.0.0.1:' + os.environ['KYO_PORT'] + '/api/health', timeout=4)"]

CMD ["python", "-m", "kyo"]
