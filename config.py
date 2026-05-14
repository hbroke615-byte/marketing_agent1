import os
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
TENANT_ID = os.getenv("TENANT_ID", "common").strip() or "common"
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
GRAPH_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Files.ReadWrite"
]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 30))
MARKETING_FOLDER_PATH = os.getenv("MARKETING_FOLDER_PATH", "Marketing")
LINKEDIN_PROFILE_URL = os.getenv(
    "LINKEDIN_PROFILE_URL",
    "https://www.linkedin.com/in/muhammad-haris-2a805b24b/"
)
