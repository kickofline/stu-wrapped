# Base and uv are pinned by digest (multi-arch indexes; the server is arm64). To update,
# resolve new digests with `docker buildx imagetools inspect <image>:<tag>`.
FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# The app runs as an unprivileged user. Its persistent volume (/app/data) must be owned
# by this uid: chown -R 10001:10001 the volume once when moving from a root image.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app

# Install Python packages exactly as locked, against the image's own Python
COPY pyproject.toml uv.lock ./
RUN UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --no-cache

# Copy application source (root-owned and read-only for the app user)
COPY . .

RUN mkdir -p data && chown app:app data

USER app

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD [".venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

# Settings (one worker, eight threads, job recovery at boot) live in gunicorn.conf.py
CMD [".venv/bin/gunicorn", "app:app"]
