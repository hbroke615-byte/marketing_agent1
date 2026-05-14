# AI Email Agent (Microsoft Graph API)

An AI assistant that monitors an Outlook inbox, drafts replies using OpenAI GPT-4o, and asks for human approval via a Tkinter GUI before sending.

## Azure Portal Setup (Required)

This application uses the **Microsoft Graph API (Device Code Flow)**. You must register an app in Azure:

1. Go to the [Azure Portal - App Registrations](https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).
2. Click **New registration**.
3. **Name**: AI Email Agent
4. **Supported account types**: Choose **"Accounts in any organizational directory and personal Microsoft accounts"**.
5. Leave Redirect URI empty.
6. Click **Register**.

### Important Settings:
1. On the Overview page, copy the **Application (client) ID**.
2. Go to **Authentication** under Manage. 
3. Scroll down to **Advanced settings** and change the **Allow public client flows** toggle to **Yes**, then click **Save**. *(This is required for device code flow!)*

## Configuration

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure `.env` file**:
   Open `.env` and fill in your Azure Client ID:
   ```
   CLIENT_ID=your_azure_client_id_here
   TENANT_ID=common
   OPENAI_API_KEY=sk-proj-YOUR_API_KEY
   POLL_INTERVAL=30
   ```
   *(Note: Client Secret is no longer needed!)*

## Authentication & Running

1. **Start the Agent**:
   ```bash
   python main.py
   ```
   The FIRST time you run this, your terminal will display a message like:
   `To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code AB12CD34 to authenticate.`

   Follow those instructions. Once you authenticate in your browser, the script will automatically continue running.

2. **Testing**:
   Send a test email to your Outlook from a different account. Within ~30 seconds, a GUI window will appear allowing you to Approve, provide Feedback to regenerate, or Cancel.
