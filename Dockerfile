# Use Playwright's official Python image — Chromium and all its system
# libraries (libxcomposite, libxdamage, etc.) are pre-installed, so we don't
# have to chase Nix package names.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APPROVAL_HOST=0.0.0.0

WORKDIR /app

# Install Python deps first so this layer is cached across code-only changes.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Chromium is already installed in the base image but the python-side
# wrappers are version-pinned to Playwright; re-run install to make sure
# `playwright` from requirements.txt and the bundled browser agree.
RUN python -m playwright install chromium

# Copy the rest of the project, including the .json state files and the
# outlook_token_cache.bin so the deployed app starts in the same state as
# local. .dockerignore controls what's actually copied.
COPY . .

# Railway injects $PORT. approval_ui.py reads it; startup.sh just execs main1.py.
EXPOSE 8787
CMD ["bash", "startup.sh"]
