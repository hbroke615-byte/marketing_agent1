# Deploying AI_Email_OK to Railway

This deploys `main1.py` (which starts the `approval_ui.py` HTTP server) so it
behaves exactly like running `python main1.py` locally — same Pipeline /
Marketing Agent / Apollo CRM tabs, same polling loop, same OneDrive +
LinkedIn integration.

GitHub source: https://github.com/hbroke615-byte/marketing_agent1
Railway dashboard: https://railway.com/dashboard

---

## Files that make this work

| File | Purpose |
| --- | --- |
| `railway.json` | Tells Railway: build with Nixpacks, run `bash startup.sh`, health-check `/`. |
| `nixpacks.toml` | Build plan: install Python 3.11 + Chromium system libs, `pip install -r requirements.txt`, then `playwright install chromium` at build time (not first-request) so cold start is fast. |
| `Procfile` | Fallback startup command for any builder that ignores `railway.json`. |
| `runtime.txt` | Pins Python 3.11. |
| `startup.sh` | Exports `APPROVAL_HOST=0.0.0.0` and execs `python -u main1.py` so it binds to Railway's injected `$PORT`. |
| `.env.example` | The env vars you must paste into Railway → Variables. The real `.env` is gitignored. |
| `.gitignore` | Excludes secrets and the venv, but **does** ship `.json` and `.bin` state files (`outlook_token_cache.bin`, `marketing_contacts.json`, `linkedin_state.json`, etc.) so the app runs in the same state as locally. |

---

## Step 1 — Push to GitHub

From this project root in PowerShell:

```powershell
git remote set-url origin https://github.com/hbroke615-byte/marketing_agent1.git
git add .gitignore railway.json nixpacks.toml Procfile runtime.txt startup.sh .env.example RAILWAY_DEPLOY.md
git add *.json *.bin
git commit -m "Add Railway deployment config"
git push -u origin main
```

If the remote rejects the push (existing repo with different history), force-push
with `git push -u origin main --force` — but only if you're sure the GitHub repo
has nothing you need to keep.

## Step 2 — Create the Railway project

1. Open https://railway.com/dashboard.
2. Click **New Project** → **Deploy from GitHub repo**.
3. Pick `hbroke615-byte/marketing_agent1`. Railway detects `railway.json` and
   `nixpacks.toml` automatically.
4. Wait for the first build (5–10 min — Chromium download is ~150 MB).

## Step 3 — Set environment variables

In the Railway project → **Variables** tab, paste each line from
[.env.example](.env.example), replacing the placeholders with your real values:

| Variable | Value |
| --- | --- |
| `CLIENT_ID` | Azure AD app client ID |
| `CLIENT_SECRET` | Azure AD app client secret |
| `TENANT_ID` | Azure AD tenant ID |
| `OPENAI_API_KEY` | `sk-...` |
| `APOLLO_API_KEY` | Apollo CRM key |
| `APIFY_API_TOKEN` | Apify token for lead scraping |
| `LINKEDIN_PROFILE_URL` | Your LinkedIn vanity URL |
| `POLL_INTERVAL` | `30` |
| `MARKETING_FOLDER_PATH` | `Marketing` |
| `APPROVAL_HOST` | `0.0.0.0` |

Do **not** set `PORT` — Railway injects it.

## Step 4 — Generate a public domain

Railway → **Settings** → **Networking** → **Generate Domain**.
You'll get something like `https://marketing-agent1-production.up.railway.app`.
That's where the approval UI (Pipeline / Marketing Agent / Apollo CRM tabs)
will be served.

## Step 5 — Watch the deploy logs

Railway → **Deployments** → click the latest deployment → **View Logs**.

You should see, in order:

1. `=== [bg] Ensuring Playwright Chromium is present ===`
2. `=== Starting main1.py on host=0.0.0.0 port=<port> ===`
3. `🔎 Using cached login for: dalbir.bains@galaxypharma.net`
4. `Authenticated successfully.`
5. `Approval web UI running at http://0.0.0.0:<port>`
6. `Checking Inbox for recent emails...`

Step 3 will print a **device-code URL** instead if `outlook_token_cache.bin`
expired (~90 days after last local login). See *Refreshing Outlook auth* below.

---

## Refreshing Outlook auth (when the token cache expires)

`outlook_token_cache.bin` is committed to git, so refreshing it means
re-running the login flow locally, then pushing:

1. Locally, delete `outlook_token_cache.bin` and run `python main1.py`. It
   prints a device-code URL — open it, paste the code, sign in.
2. After "Login successful! Token cached." appears, stop the app (`Ctrl+C`).
3. `git add outlook_token_cache.bin && git commit -m "Refresh Outlook token" && git push`
4. Railway auto-deploys on push.

---

## Refreshing LinkedIn session (every ~30 days)

Same pattern, for `linkedin_state.json`:

1. Locally, run the LinkedIn login script that produced your current
   `linkedin_state.json` (Playwright headed, sign in by hand).
2. `git add linkedin_state.json && git commit -m "Refresh LinkedIn session" && git push`

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Health check fails | App didn't bind to `$PORT` in time — usually MSAL device-code prompt blocking startup | Look at logs. If you see a device-code URL, refresh the token cache (above). |
| `playwright._impl._api_types.Error: Executable doesn't exist` | Chromium install was skipped or failed | Check build logs for `playwright install chromium`. If it failed, redeploy. The runtime `startup.sh` retry will also try to fetch it on boot. |
| LinkedIn DMs fail with "checkpoint" / "verify it's you" | Datacenter IP flagged by LinkedIn | Use a residential proxy (Railway add-on or external) or expect LinkedIn automation from cloud to be fragile. |
| `.json` data files reset on redeploy | A redeploy overwrites the live data files with whatever's in git | Either commit the current state before redeploying, or move the json/bin files to a Railway Volume (Project → Storage) and mount it at the project root. |
| Polling loop dies after some time | Container restarted (Railway free tier has limits) | Upgrade to a paid plan, or set `restartPolicyType: ALWAYS` in `railway.json`. |

---

## Redeploying (after code changes)

Railway auto-deploys on every push to `main`:

```powershell
git add <files>
git commit -m "your message"
git push
```

Config and Variables persist across deploys.
