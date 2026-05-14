import msal
import requests
import os
import json
from urllib.parse import quote

TOKEN_CACHE_FILE = "outlook_token_cache.bin"

class Graph:
    def __init__(self, client_id, authority, scopes):
        self.scopes = scopes
        self.cache = msal.SerializableTokenCache()
        
        # Load existing cache if present
        if os.path.exists(TOKEN_CACHE_FILE):
            with open(TOKEN_CACHE_FILE, "r") as f:
                self.cache.deserialize(f.read())
                
        self.app = msal.PublicClientApplication(
            client_id=client_id,
            authority=authority,
            token_cache=self.cache
        )

    def _get_access_token(self):
        # Try to get token from cache first
        accounts = self.app.get_accounts()
        if accounts:
            username = accounts[0].get("username") or accounts[0].get("home_account_id") or "unknown"
            print(f"🔎 Using cached login for: {username}")
            result = self.app.acquire_token_silent(self.scopes, account=accounts[0])
            if result and "access_token" in result:
                return result["access_token"]

        # If no token in cache, do device flow
        print("\n🔑 Starting device code login...\n")
        flow = self.app.initiate_device_flow(scopes=self.scopes)
        if "user_code" not in flow:
            raise Exception("Failed to start device flow: " + json.dumps(flow, indent=2))

        print(flow["message"])
        print("\n→ Open the URL above in your browser and enter the code.\n")
        
        result = self.app.acquire_token_by_device_flow(flow)
        if "access_token" in result:
            with open(TOKEN_CACHE_FILE, "w") as f:
                f.write(self.cache.serialize())
            print("✅ Login successful! Token cached.\n")
            return result["access_token"]
        else:
            raise Exception("Login failed: " + json.dumps(result, indent=2))

    def get_me(self):
        """Return signed-in user profile from Graph (needs User.Read scope for full fields)."""
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            "https://graph.microsoft.com/v1.0/me?$select=displayName,mail,userPrincipalName",
            headers=headers,
        )
        if response.status_code == 200:
            return response.json()
        print(
            f"❌ Error calling /me ({response.status_code}): {response.text[:800]}"
        )
        return None

    # def fetch_unread_emails(self):
    #     token = self._get_access_token()
    #     headers = {
    #         "Authorization": f"Bearer {token}",
    #         "Prefer": 'outlook.body-content-type="text"'
    #     }
        
    #     url = (
    #         "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
    #         "?$filter=isRead eq false"
    #         "&$top=10"
    #         "&$orderby=receivedDateTime desc"
    #         "&$select=subject,from,receivedDateTime,bodyPreview,body,conversationId,hasAttachments"
    #     )
        
    #     response = requests.get(url, headers=headers)
    #     if response.status_code == 200:
    #         data = response.json()
    #         emails_data = []
    #         if data.get("value"):
    #             for email in data["value"]:
    #                 sender_address = email.get('from', {}).get('emailAddress', {}).get('address', 'Unknown')
                    
    #                 # We can use bodyPreview or actual body content
    #                 body_content = ""
    #                 if email.get('body') and email['body'].get('content'):
    #                     # Simplistic extraction for plain text, or fallback to preview
    #                     body_content = email['body']['content']
    #                 else:
    #                     body_content = email.get('bodyPreview', '')

    #                 emails_data.append({
    #                     "id": email.get('id'),
    #                     "conversation_id": email.get('conversationId'),
    #                     "has_attachments": email.get('hasAttachments', False),
    #                     "sender": sender_address,
    #                     "subject": email.get('subject', 'No Subject'),
    #                     "body": body_content
    #                 })
    #         return emails_data
    #     else:
    #         print(f"❌ Error fetching emails ({response.status_code}): {response.text[:800]}")
    #         return []
    def fetch_unread_emails(self):
            token = self._get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Prefer": 'outlook.body-content-type="text"'
            }
            
            url = (
                "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
                "?$filter=isRead eq false"
                "&$top=10"
                "&$orderby=receivedDateTime desc"
                "&$select=subject,from,receivedDateTime,bodyPreview,body,conversationId,hasAttachments"
            )
            
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                emails_data = []
                if data.get("value"):
                    for email in data["value"]:
                        sender_address = email.get('from', {}).get('emailAddress', {}).get('address', 'Unknown')
                        
                        # We can use bodyPreview or actual body content
                        body_content = ""
                        if email.get('body') and email['body'].get('content'):
                            # Simplistic extraction for plain text, or fallback to preview
                            body_content = email['body']['content']
                        else:
                            body_content = email.get('bodyPreview', '')

                        emails_data.append({
                            "id": email.get('id'),
                            "conversation_id": email.get('conversationId'),
                            "has_attachments": email.get('hasAttachments', False),
                            "sender": sender_address,
                            "subject": email.get('subject', 'No Subject'),
                            "body": body_content
                        })
                return emails_data
            else:
                print(f"❌ Error fetching emails ({response.status_code}): {response.text[:800]}")
                return []

    def fetch_recent_emails(self, top=10):
        """
        Fetch most recent emails from Inbox, regardless of read state.
        """
        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Prefer": 'outlook.body-content-type="text"',
        }

        url = (
            "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
            f"?$top={int(top)}"
            "&$orderby=receivedDateTime desc"
            "&$select=id,subject,from,receivedDateTime,bodyPreview,body,conversationId,hasAttachments,isRead"
        )

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            emails_data = []
            if data.get("value"):
                for email in data["value"]:
                    sender_address = (
                        email.get("from", {})
                        .get("emailAddress", {})
                        .get("address", "Unknown")
                    )

                    body_content = ""
                    if email.get("body") and email["body"].get("content"):
                        body_content = email["body"]["content"]
                    else:
                        body_content = email.get("bodyPreview", "")

                    emails_data.append(
                        {
                            "id": email.get("id"),
                            "conversation_id": email.get("conversationId"),
                            "has_attachments": email.get("hasAttachments", False),
                            "sender": sender_address,
                            "subject": email.get("subject", "No Subject"),
                            "body": body_content,
                            "is_read": bool(email.get("isRead", False)),
                            "receivedDateTime": email.get("receivedDateTime", ""),
                        }
                    )
            return emails_data
        print(
            f"❌ Error fetching recent emails ({response.status_code}): "
            f"{response.text[:800]}"
        )
        return []

    def fetch_conversation_history(self, conversation_id):
        if not conversation_id:
            return []
            
        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Prefer": 'outlook.body-content-type="text"'
        }
        
        # We fetch emails in the thread and sort them in Python to avoid Graph API InefficientFilter errors.
        url = (
            f"https://graph.microsoft.com/v1.0/me/messages"
            f"?$filter=conversationId eq '{conversation_id}'"
            f"&$select=subject,from,receivedDateTime,bodyPreview,body"
        )
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            history = []
            if data.get("value"):
                for email in data["value"]:
                    sender_address = email.get('from', {}).get('emailAddress', {}).get('address', 'Unknown')
                    
                    body_content = ""
                    if email.get('body') and email['body'].get('content'):
                        body_content = email['body']['content']
                    else:
                        body_content = email.get('bodyPreview', '')
                    
                    history.append({
                        "sender": sender_address,
                        "subject": email.get('subject', 'No Subject'),
                        "body": body_content,
                        "receivedDateTime": email.get('receivedDateTime', '')
                    })
            
            # Sort by receivedDateTime descending, grab top 5, then reverse to chronological order
            history.sort(key=lambda x: x["receivedDateTime"], reverse=True)
            history = history[:5]
            return history[::-1]
        else:
            print(f"❌ Error fetching history ({response.status_code}): {response.text[:800]}")
            return []

    # ─────────────────────────────────────────────────────────────────
    # Attachment helpers
    # ─────────────────────────────────────────────────────────────────
    def fetch_email_attachments(self, email_id):
        """Return list of attachment metadata dicts for a given email."""
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}/attachments?$select=id,name,contentType,size"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get("value", [])
        print(f"❌ Error fetching attachments ({response.status_code}): {response.text[:400]}")
        return []

    def download_attachment_bytes(self, email_id, attachment_id):
        """Download and return raw bytes of a specific attachment."""
        import base64
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}/attachments/{attachment_id}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            b64 = data.get("contentBytes", "")
            if b64:
                return base64.b64decode(b64)
        print(f"❌ Error downloading attachment ({response.status_code}): {response.text[:400]}")
        return None

    # ─────────────────────────────────────────────────────────────────
    # OneDrive helpers
    # ─────────────────────────────────────────────────────────────────
    def list_onedrive_folder(self, folder_path, recursive=False):
        """List files in a OneDrive folder, optionally including subfolders."""
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        def item_name(item):
            return (item.get("name") or "").strip()

        def list_children_by_url(url, display_path):
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                files = []
                for item in response.json().get("value", []):
                    if "file" in item:
                        files.append(item)
                    elif recursive and "folder" in item:
                        child_path = f"{display_path}/{item_name(item)}"
                        child_url = (
                            "https://graph.microsoft.com/v1.0/me/drive/items/"
                            f"{item['id']}/children"
                        )
                        files.extend(list_children_by_url(child_url, child_path))
                return files
            if response.status_code == 404:
                return None

            print(
                f"Error listing folder '{display_path}' ({response.status_code}): "
                f"{response.text[:400]}"
            )
            return []

        cleaned_path = folder_path.strip().strip("/")
        encoded_path = quote(cleaned_path, safe="/")
        direct_url = (
            "https://graph.microsoft.com/v1.0/me/drive/root:"
            f"/{encoded_path}:/children"
        )
        direct_files = list_children_by_url(direct_url, cleaned_path)
        if direct_files is not None:
            return direct_files

        root_response = requests.get(
            "https://graph.microsoft.com/v1.0/me/drive/root/children",
            headers=headers,
        )
        if root_response.status_code != 200:
            print(
                "Could not list OneDrive root folders "
                f"({root_response.status_code}): {root_response.text[:400]}"
            )
            return []

        path_parts = [part.strip() for part in cleaned_path.split("/") if part.strip()]
        current_items = root_response.json().get("value", [])
        current_folder = None

        for index, part in enumerate(path_parts):
            current_folder = next(
                (
                    item
                    for item in current_items
                    if "folder" in item and item_name(item).lower() == part.lower()
                ),
                None,
            )
            if not current_folder:
                if index == 0:
                    available = [
                        item_name(item) for item in current_items if "folder" in item
                    ]
                    print(
                        f"OneDrive folder '{folder_path}' not found. "
                        f"Root folders visible to this login: {available}"
                    )
                else:
                    print(f"OneDrive folder path '{folder_path}' not found at '{part}'.")
                return []

            children_url = (
                "https://graph.microsoft.com/v1.0/me/drive/items/"
                f"{current_folder['id']}/children"
            )
            if index == len(path_parts) - 1:
                files = list_children_by_url(children_url, cleaned_path)
                return files or []

            child_response = requests.get(children_url, headers=headers)
            if child_response.status_code != 200:
                print(
                    f"Could not list OneDrive folder '{item_name(current_folder)}' "
                    f"({child_response.status_code}): {child_response.text[:400]}"
                )
                return []
            current_items = child_response.json().get("value", [])

        return []

    def download_onedrive_file(self, download_url):
        """Download raw bytes from a OneDrive downloadUrl."""
        response = requests.get(download_url)
        if response.status_code == 200:
            return response.content
        print(f"❌ Error downloading file ({response.status_code}): {response.text[:400]}")
        return None

    def mark_as_read(self, email_id):

        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}"
        payload = {"isRead": True}
        
        response = requests.patch(url, headers=headers, json=payload)
        if response.status_code not in [200, 204]:
            print(f"Failed to mark email as read: {response.text[:800]}")

    def send_reply(self, email_id, body_text):
        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}/reply"
        payload = {
            "message": {
                "body": {
                    "contentType": "Text",
                    "content": body_text
                }
            }
        }
        
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code in [200, 202, 204]:
            return True
        else:
            print(f"Failed to send reply: {response.text[:800]}")
            return False
