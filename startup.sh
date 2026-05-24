#!/bin/bash
# Startup script for both Azure App Service and Railway.
# Do NOT use `set -e` — we want to keep going even if Chromium install fails.

# If Chromium wasn't baked in at build time (Azure path), fetch it in the
# background so the HTTP server starts before the platform's warmup probe
# times out. On Railway, nixpacks.toml runs `playwright install chromium`
# during build, so this becomes a fast no-op.
(
  echo "=== [bg] Ensuring Playwright Chromium is present ==="
  python -m playwright install chromium 2>&1
  echo "=== [bg] Chromium check finished with exit $? ==="
) &

# Bind to all interfaces so the platform router can reach us, and use the
# platform-assigned PORT (Railway/Azure both set this). approval_ui.py already
# reads $PORT — we just need to override the localhost default.
export APPROVAL_HOST=0.0.0.0
echo "=== Starting main1.py on host=0.0.0.0 port=${PORT:-8787} ==="
exec python -u main1.py
