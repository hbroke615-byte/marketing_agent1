#!/bin/bash
# Do NOT use `set -e` — we want to keep going even if Chromium install fails.

# Chromium (for LinkedIn DM via Playwright) is fetched in the background so the
# HTTP server starts before the App Service warmup probe times out at 230s.
# Skip --with-deps; the App Service Python image already has the needed libs.
(
  echo "=== [bg] Installing Playwright Chromium ==="
  python -m playwright install chromium 2>&1
  echo "=== [bg] Chromium install finished with exit $? ==="
) &

echo "=== Starting main1.py on host=0.0.0.0 port=$PORT ==="
export APPROVAL_HOST=0.0.0.0
exec python -u main1.py
