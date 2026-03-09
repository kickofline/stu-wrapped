# ── Base ──────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Install system packages needed by Playwright's Chromium and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Copy project files
COPY pyproject.toml uv.lock* ./

# Install Python packages with uv
RUN /root/.local/bin/uv sync --frozen --no-dev

# Install Playwright's Chromium browser and its OS dependencies
RUN playwright install chromium --with-deps

# Copy application source
COPY . .

# Ensure data directory exists (mount a persistent volume at /app/data in Coolify)
RUN mkdir -p data

ENV PORT=8000
EXPOSE 8000

# 1 worker, 8 threads — jobs are in-memory so multiple workers would lose state
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 120 app:app"]
