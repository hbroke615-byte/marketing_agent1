import os
import threading
import time
from typing import List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai_agent
import marketing_campaign_agent
import onedrive_agent
import send_dm
from config import (
    AUTHORITY,
    CLIENT_ID,
    GRAPH_SCOPES,
    MARKETING_FOLDER_PATH,
    POLL_INTERVAL,
)
from graph import Graph

app = FastAPI()

if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def read_root():
    return RedirectResponse(url="/static/index.html")


graph = Graph(CLIENT_ID, AUTHORITY, GRAPH_SCOPES)
RECENT_MARKETING_MESSAGES = []
MAX_RECENT_MARKETING_MESSAGES = 50


def remember_marketing_messages(messages):
    if not messages:
        return

    RECENT_MARKETING_MESSAGES.extend(messages)
    if len(RECENT_MARKETING_MESSAGES) > MAX_RECENT_MARKETING_MESSAGES:
        del RECENT_MARKETING_MESSAGES[:-MAX_RECENT_MARKETING_MESSAGES]

    for message in messages:
        linkedin_message = message.get("linkedin_message", "")
        if not linkedin_message:
            continue

        sent = send_dm.send_dm(linkedin_message)
        message["linkedin_sent"] = sent


def background_onedrive_polling():
    while True:
        try:
            messages = onedrive_agent.poll_folder(graph, MARKETING_FOLDER_PATH)
            remember_marketing_messages(messages)
        except Exception as e:
            print(f"Error in OneDrive polling loop: {e}")
        time.sleep(POLL_INTERVAL)


@app.on_event("startup")
def startup_event():
    print("=== Starting Outlook Add-in Server ===")

    try:
        graph._get_access_token()
        print("Graph API authenticated successfully.")
    except Exception as e:
        print(f"Failed to authenticate: {e}")
        return

    threading.Thread(target=background_onedrive_polling, daemon=True).start()
    print(
        "OneDrive marketing campaign polling started for "
        f"folder '{MARKETING_FOLDER_PATH}'."
    )


def handle_attachments(graph_client, email_data):
    """
    Checks email attachments for marketing product documents.
    Returns attachment text for normal email drafting plus LinkedIn campaign messages.
    """
    attachments = graph_client.fetch_email_attachments(email_data["id"])
    if not attachments:
        return "", []

    print(
        f"   Found {len(attachments)} attachment(s). "
        "Scanning for marketing product documents..."
    )
    all_attachment_texts = []
    marketing_messages = []

    for attachment in attachments:
        attachment_id = attachment.get("id")
        attachment_name = attachment.get("name", "unknown")
        content_type = attachment.get("contentType", "")

        print(f"   Analyzing: {attachment_name}")
        file_bytes = graph_client.download_attachment_bytes(
            email_data["id"], attachment_id
        )
        if not file_bytes:
            continue

        attachment_text = marketing_campaign_agent.extract_text(
            file_bytes, attachment_name, content_type
        )
        if attachment_text.strip():
            all_attachment_texts.append(
                f"[Attachment: {attachment_name}]\n{attachment_text.strip()}"
            )

        result = marketing_campaign_agent.analyze_attachment(
            file_bytes, attachment_name, content_type
        )
        if result is None:
            continue

        if not result.is_marketing_product_document:
            print(
                f"   '{attachment_name}' is not a marketing product document. "
                "Skipping campaign generation."
            )
            continue

        print(f"   Marketing product document detected in '{attachment_name}'.")
        print(f"      Product Name  : {result.product_name}")
        print(f"      Audience      : {result.target_audience}")
        print(f"      Campaign Goal : {result.campaign_goal}")

        marketing_messages.append(
            {
                "file_name": attachment_name,
                "linkedin_message": result.linkedin_message or "",
                "analysis": result.model_dump()
                if hasattr(result, "model_dump")
                else result.dict(),
            }
        )

    return "\n\n".join(all_attachment_texts), marketing_messages


class DraftRequest(BaseModel):
    sender: str
    subject: str
    body: str
    email_id: Optional[str] = None
    conversation_id: Optional[str] = None
    attachment_names: Optional[List[str]] = None


class SendRequest(BaseModel):
    email_id: str
    draft_body: str


class MarketingPromptBody(BaseModel):
    prompt: str


@app.post("/api/generate")
def generate_draft(req: DraftRequest):
    """Generate either a LinkedIn marketing message or a normal AI email reply."""
    try:
        print("\n[NEW DRAFT REQUEST]")
        print(f"   From: {req.sender}")
        print(f"   Subject: {req.subject}")

        email_data = {
            "id": req.email_id,
            "sender": req.sender,
            "subject": req.subject,
            "body": req.body,
        }

        attachment_context = ""
        marketing_messages = []
        rest_conversation_id = req.conversation_id

        if req.email_id:
            attachment_context, marketing_messages = handle_attachments(graph, email_data)

            try:
                token = graph._get_access_token()
                headers = {"Authorization": f"Bearer {token}"}
                url = (
                    "https://graph.microsoft.com/v1.0/me/messages/"
                    f"{req.email_id}?$select=conversationId"
                )
                response = requests.get(url, headers=headers)
                if response.status_code == 200:
                    rest_conversation_id = response.json().get(
                        "conversationId", req.conversation_id
                    )
            except Exception as e:
                print(f"Could not fetch REST conversation_id: {e}")

        if marketing_messages:
            draft = "\n\n".join(
                message["linkedin_message"]
                for message in marketing_messages
                if message.get("linkedin_message")
            )
            remember_marketing_messages(marketing_messages)
            return {
                "status": "success",
                "draft": draft,
                "marketing_messages": marketing_messages,
            }

        history = []
        if rest_conversation_id:
            history = graph.fetch_conversation_history(rest_conversation_id)

        print("Generating AI draft...")
        draft = ai_agent.generate_draft(
            email_data, history=history, attachment_context=attachment_context
        )
        return {"status": "success", "draft": draft, "marketing_messages": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/marketing/poll")
def poll_marketing_onedrive():
    """Poll OneDrive now and return any new LinkedIn campaign messages."""
    try:
        messages = onedrive_agent.poll_folder(graph, MARKETING_FOLDER_PATH)
        remember_marketing_messages(messages)
        return {
            "status": "success",
            "folder": MARKETING_FOLDER_PATH,
            "messages": messages,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/marketing/prompt")
def get_marketing_system_prompt():
    """Return the current marketing document analysis system prompt (default or saved)."""
    return {
        "status": "success",
        "prompt": marketing_campaign_agent.get_marketing_system_prompt(),
    }


@app.post("/api/marketing/prompt")
def save_marketing_system_prompt(body: MarketingPromptBody):
    """Save a custom system prompt used for attachment and OneDrive marketing analysis."""
    if not body.prompt or not body.prompt.strip():
        raise HTTPException(
            status_code=400, detail="Prompt cannot be empty."
        )
    marketing_campaign_agent.set_marketing_system_prompt(body.prompt)
    return {
        "status": "success",
        "prompt": marketing_campaign_agent.get_marketing_system_prompt(),
    }


@app.get("/api/marketing/messages")
def get_recent_marketing_messages():
    """Return recently generated marketing campaign messages."""
    return {
        "status": "success",
        "folder": MARKETING_FOLDER_PATH,
        "messages": RECENT_MARKETING_MESSAGES,
    }


@app.post("/api/send")
def send_email(req: SendRequest):
    """Send the approved draft."""
    try:
        print("\n[SENDING REPLY]")
        print("   Draft approved. Sending email reply via Graph API...")

        success = graph.send_reply(req.email_id, req.draft_body)
        if success:
            graph.mark_as_read(req.email_id)
            print("   Reply sent successfully. Marked as read.")
            return {"status": "success"}

        print("   Failed to send reply.")
        raise HTTPException(status_code=500, detail="Failed to send reply.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    cert_path = os.path.expanduser("~/.office-addin-dev-certs/localhost.crt")
    key_path = os.path.expanduser("~/.office-addin-dev-certs/localhost.key")

    if os.path.exists(cert_path) and os.path.exists(key_path):
        print("Starting secure HTTPS server using Office Dev Certs...")
        uvicorn.run(
            "server:app",
            host="127.0.0.1",
            port=8000,
            reload=True,
            ssl_keyfile=key_path,
            ssl_certfile=cert_path,
        )
    else:
        print("Starting unencrypted HTTP server...")
        uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
