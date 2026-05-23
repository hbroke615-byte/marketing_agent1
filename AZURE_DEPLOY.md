# Deploying AI_Email_OK to Azure App Service (Linux, B1)

This guide is a one-shot deployment via Azure CLI. After the first deploy works,
subsequent deploys are just re-running step 4.

## Prereqs (one-time)

1. **Install the Azure CLI**: https://learn.microsoft.com/cli/azure/install-azure-cli-windows
2. **Sign in** in PowerShell:

   ```powershell
   az login
   ```

   A browser opens for sign-in. Pick the subscription you want to use.

3. **Confirm the subscription** that's active:

   ```powershell
   az account show --query "{name:name, id:id}" -o table
   ```

4. **Pick a region** (the rest of this guide uses `eastus`; change if you like).

## Variables you'll reuse (paste these once per shell session)

```powershell
$RG       = "rg-ai-email-ok"
$LOCATION = "eastus"
$PLAN     = "asp-ai-email-ok"
$APP      = "ai-email-ok-$(Get-Random -Maximum 9999)"   # must be globally unique
$RUNTIME  = "PYTHON:3.10"
```

Take a note of the value of `$APP` — that's your public URL: `https://<APP>.azurewebsites.net`.

---

## Step 1 — Create the resource group + App Service plan

```powershell
az group create --name $RG --location $LOCATION

az appservice plan create `
    --name $PLAN `
    --resource-group $RG `
    --is-linux `
    --sku B1
```

`B1` is the cheapest tier that supports **Always On** (required so your polling
loop stays alive when no HTTP traffic is hitting the app). ~$13/month.

## Step 2 — Create the web app

```powershell
az webapp create `
    --resource-group $RG `
    --plan $PLAN `
    --name $APP `
    --runtime $RUNTIME
```

## Step 3 — Configure the app

### 3a. Startup command + Always On + build hook

```powershell
# Tell App Service to run startup.sh (installs Chromium, launches main1.py)
az webapp config set `
    --resource-group $RG --name $APP `
    --startup-file "bash startup.sh" `
    --always-on true

# Make App Service run `pip install -r requirements.txt` on every deploy
az webapp config appsettings set `
    --resource-group $RG --name $APP `
    --settings SCM_DO_BUILD_DURING_DEPLOYMENT=1
```

### 3b. Upload your secrets as App Settings (do NOT upload the .env file)

Replace each `<value>` with the actual value from your local [.env](.env).

```powershell
az webapp config appsettings set `
    --resource-group $RG --name $APP `
    --settings `
        OPENAI_API_KEY="<your-openai-key>" `
        APIFY_API_TOKEN="<your-apify-token>" `
        APOLLO_API_KEY="<your-apollo-key-if-still-used>" `
        CLIENT_ID="<your-azure-ad-client-id>" `
        CLIENT_SECRET="<your-azure-ad-client-secret>" `
        TENANT_ID="<your-azure-ad-tenant-id>" `
        LINKEDIN_PROFILE_URL="<your-default-linkedin-profile-url>" `
        POLL_INTERVAL="30" `
        APPROVAL_HOST="0.0.0.0"
```

**Important:** these become real environment variables inside the container, so
`os.getenv("OPENAI_API_KEY")` etc. work exactly like locally. The `.env` file
itself stays only on your laptop.

> Optional: if you want to override the default Apify actor, also set
> `APIFY_LEADS_ACTOR="code_crafter/leads-finder"`.

## Step 4 — Build the deployment zip and push it

From this project root, in PowerShell:

```powershell
# Builds deploy.zip with only the files Azure needs
.\package_for_azure.ps1

# Push it to App Service
az webapp deploy `
    --resource-group $RG --name $APP `
    --src-path deploy.zip `
    --type zip
```

The first deploy takes ~5–10 minutes because Chromium has to be downloaded by
[startup.sh](startup.sh) on first boot (~150 MB). Subsequent deploys reuse it.

## Step 5 — Watch the startup log

```powershell
az webapp log tail --resource-group $RG --name $APP
```

You should see (in order):

1. `=== Installing Playwright Chromium ===`
2. `=== Starting main1.py on host=0.0.0.0 port=<port> ===`
3. `🔎 Using cached login for: dalbir.bains@galaxypharma.net`
4. `Authenticated successfully.`
5. `Approval web UI running at http://0.0.0.0:<port>`
6. `Checking Inbox for recent emails...`

If you see step 3 print a device-code URL instead of "Using cached login", it
means the cached refresh token expired (this happens ~90 days after your last
local login). See **Refreshing Outlook auth** below.

## Step 6 — Open the UI

Visit `https://<APP>.azurewebsites.net` in your browser. Same Pipeline /
Marketing Agent / Apollo CRM tabs as locally.

---

## Refreshing Outlook auth (when the cached token expires)

The token cache lives at `/home/site/wwwroot/outlook_token_cache.bin` on the
web app. When it expires, re-do the device-code flow locally and re-upload:

1. Locally, delete your local `outlook_token_cache.bin` and run `python main1.py`.
   It will print a device-code URL — open it in a browser, paste the code, sign in.
2. After "Login successful! Token cached." appears, stop the app.
3. Re-run `.\package_for_azure.ps1` (the new `.bin` is included in the zip).
4. Re-run `az webapp deploy --resource-group $RG --name $APP --src-path deploy.zip --type zip`.

---

## Refreshing LinkedIn session (every ~30 days)

Same pattern, for [linkedin_state.json](linkedin_state.json):

1. Locally, run the LinkedIn login script that produced your current
   `linkedin_state.json` (Playwright headed mode, sign in by hand once).
2. Re-package and re-deploy.

---

## Troubleshooting cheat sheet

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| 502 Bad Gateway after deploy | App didn't bind to `$PORT` in time | `az webapp log tail` — usually MSAL device-code prompt hanging. Refresh token cache. |
| `playwright._impl._api_types.Error: Executable doesn't exist` | Chromium install failed | SSH into App Service and run `python -m playwright install --with-deps chromium` |
| LinkedIn DMs fail with "checkpoint" | Datacenter IP flagged by LinkedIn | Use a residential proxy or accept that LinkedIn automation from cloud is fragile |
| `.json` data files reset on deploy | The zip overwrote the live data | They live in `/home/site/wwwroot` which is part of the zip target. Either exclude them from `package_for_azure.ps1` after the first deploy, or accept overwrite |
| Polling loop stops after some hours | Always On not enabled | `az webapp config set --always-on true` |

---

## Redeploying (after code changes)

```powershell
.\package_for_azure.ps1
az webapp deploy --resource-group $RG --name $APP --src-path deploy.zip --type zip
```

That's it. Config and secrets persist; only the code is replaced.

---

## Tear-down (if you need to delete everything)

```powershell
az group delete --name $RG --yes --no-wait
```

This removes the App Service, plan, and any logs. Resource group deletion is
irreversible — only run this when you mean it.
