# ── Base ──────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# curl + certificates to install uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Copy project files
COPY pyproject.toml uv.lock* ./

# Install Python packages with uv
RUN /root/.local/bin/uv sync --frozen --no-dev

# Copy application source
COPY . .

# Ensure data directory exists (mount a persistent volume at /app/data in Coolify)
RUN mkdir -p data

ENV PORT=8000
EXPOSE 8000

# 1 worker, 8 threads — jobs are in-memory so multiple workers would lose state
CMD [".venv/bin/gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", "--timeout", "120", "app:app"]
