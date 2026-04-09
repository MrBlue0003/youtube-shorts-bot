# ══════════════════════════════════════════════════════════════════
#  YouTube Shorts Bot — Dockerfile
#  Base: python:3.11-slim  |  Includes: ffmpeg
# ══════════════════════════════════════════════════════════════════

FROM python:3.11-slim

# ── System dependencies ────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies (cached layer) ────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ───────────────────────────────────────────────
COPY . .

# ── Create runtime directories ─────────────────────────────────────
RUN mkdir -p videos shorts logs music

# ── Default command ────────────────────────────────────────────────
#    Railway overrides this via the cron command in railway.toml,
#    but keep it useful for manual docker run.
CMD ["python", "main.py"]
