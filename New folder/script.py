import msal
import requests
import os
import json
import sys

# ========================= CONFIG =========================
CLIENT_ID = "e6efa876-26a6-4776-a03e-eb58eca56b6c"
AUTHORITY = "https://login.microsoftonline.com/common"   # Best choice here
SCOPES = ["https://graph.microsoft.com/Mail.Read"]
TOKEN_CACHE_FILE = "outlook_token_cache.bin"
# =========================================================

# Clean old cache
if os.path.exists(TOKEN_CACHE_FILE):
    try:
        os.remove(TOKEN_CACHE_FILE)
        print("🗑️ Old token cache deleted.")
    except:
        pass

cache = msal.SerializableTokenCache()

app = msal.PublicClientApplication(
    client_id=CLIENT_ID,
    authority=AUTHORITY,
    token_cache=cache
)

print("🔑 Starting device code login for m.usamatekhqs@outlook.com...\n")

flow = app.initiate_device_flow(scopes=SCOPES)

if "user_code" not in flow:
    print("❌ Failed to start device flow:")
    print(json.dumps(flow, indent=2))
    print("\n💡 Fixes to try:")
    print("   • Set requestedAccessTokenVersion = 2 in Manifest")
    print("   • Enable 'Allow public client flows' = Yes")
    print("   • Add redirect URI: https://login.microsoftonline.com/common/oauth2/nativeclient")
    sys.exit(1)

print(flow["message"])
print("\n→ Open the URL above in your browser and enter the code.\n")
sys.stdout.flush()

result = app.acquire_token_by_device_flow(flow)

if "access_token" in result:
    with open(TOKEN_CACHE_FILE, "w") as f:
        f.write(cache.serialize())
    print("✅ Login successful! Token cached.\n")
else:
    print("❌ Login failed:")
    print(json.dumps(result, indent=2))
    sys.exit(1)

# ===================== FETCH LATEST EMAIL =====================
print("📬 Fetching the latest email from your Inbox...\n")

headers = {"Authorization": f"Bearer {result['access_token']}"}

url = (
    "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
    "?$top=1"
    "&$orderby=receivedDateTime desc"
    "&$select=subject,from,receivedDateTime,bodyPreview"
)

response = requests.get(url, headers=headers)

if response.status_code == 200:
    data = response.json()
    if data.get("value"):
        email = data["value"][0]
        print("✅ LATEST EMAIL FOUND:")
        print("=" * 80)
        print(f"Subject     : {email.get('subject', 'No Subject')}")
        print(f"From        : {email['from']['emailAddress'].get('address', 'N/A')}")
        print(f"Received    : {email.get('receivedDateTime', 'N/A')}")
        print("-" * 80)
        print("Preview:")
        preview = email.get('bodyPreview', 'No preview available')
        print(preview[:1000])
        if len(preview) > 1000:
            print("...")
        print("=" * 80)
    else:
        print("📭 Your Inbox is empty.")
else:
    print(f"❌ Error fetching email ({response.status_code}):")
    print(response.text[:800])

print("\n🎉 Done! Delete the cache file only if you want to re-login.")