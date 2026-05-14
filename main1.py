import time

import ai_agent
import marketing_campaign_agent
import onedrive_agent
import send_dm
from approval_ui import (
    enqueue_approval,
    ensure_approval_server,
    pop_completed_approvals,
    update_inbox_stats,
)
from config import (
    AUTHORITY,
    CLIENT_ID,
    GRAPH_SCOPES,
    MARKETING_FOLDER_PATH,
    POLL_INTERVAL,
)
from graph import Graph

PROCESSED_EMAILS_JSON = "processed_email_ids.json"


def _load_processed_email_ids():
    try:
        import json
        import os

        if os.path.exists(PROCESSED_EMAILS_JSON):
            with open(PROCESSED_EMAILS_JSON, "r", encoding="utf-8") as f:
                return set(json.load(f) or [])
    except Exception as e:
        print(f"Could not load processed email IDs: {e}")
    return set()


def _save_processed_email_ids(processed_ids):
    try:
        import json

        with open(PROCESSED_EMAILS_JSON, "w", encoding="utf-8") as f:
            json.dump(sorted(processed_ids), f)
    except Exception as e:
        print(f"Could not save processed email IDs: {e}")


def handle_attachments(graph_client, email_data):
    """
    Checks email attachments for marketing product documents.
    Returns attachment text for normal drafting plus LinkedIn campaign messages.
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

        if result.is_marketing_product_document:
            print(f"   Marketing product document detected in '{attachment_name}'.")
            linkedin_message = result.linkedin_message or ""
            if linkedin_message:
                send_dm.send_dm(linkedin_message)
            marketing_messages.append(linkedin_message)
        else:
            print(f"   '{attachment_name}' is not a marketing product document.")

    return "\n\n".join(all_attachment_texts), marketing_messages


def main():
    print("Starting AI Email Agent (MSAL + Requests)...")

    if not CLIENT_ID or CLIENT_ID == "your_azure_client_id_here":
        print("Please update .env with your CLIENT_ID")
        return

    graph = Graph(CLIENT_ID, AUTHORITY, GRAPH_SCOPES)

    try:
        graph._get_access_token()
        print("Authenticated successfully.")
    except Exception as e:
        print(f"Failed to authenticate: {e}")
        return

    me = graph.get_me()
    if me:
        print(
            "✅ Microsoft Graph mailbox identity: "
            f"displayName='{me.get('displayName')}', "
            f"mail='{me.get('mail')}', "
            f"upn='{me.get('userPrincipalName')}'"
        )

    print(f"Polling INBOX and OneDrive every {POLL_INTERVAL} seconds...")
    print(f"Marketing OneDrive folder: {MARKETING_FOLDER_PATH}")

    processed_ids = _load_processed_email_ids()
    ensure_approval_server()

    while True:
        try:
            for action in pop_completed_approvals():
                email_data = action["email_data"]
                if action["approved"] and action.get("final_draft"):
                    print(
                        f"Sending approved reply to {email_data.get('sender')} "
                        f"(subject: {email_data.get('subject')})..."
                    )
                    success = graph.send_reply(email_data["id"], action["final_draft"])
                    if success:
                        print("Reply sent successfully!")
                        graph.mark_as_read(email_data["id"])
                    else:
                        print("Failed to send reply.")
                else:
                    print(
                        f"Approval cancelled for {email_data.get('sender')} — "
                        f"{email_data.get('subject')}"
                    )
                    graph.mark_as_read(email_data["id"])

            print("\nChecking Inbox for recent emails...")
            recent_emails = graph.fetch_recent_emails(top=10)
            print(f"Fetched {len(recent_emails)} recent message(s).")
            unread_emails = graph.fetch_unread_emails()
            update_inbox_stats(len(unread_emails), len(recent_emails))

            new_emails = [
                email
                for email in recent_emails
                if email.get("id") and email.get("id") not in processed_ids
            ]

            if not new_emails:
                print("No new (unprocessed) emails found.")

            for email_data in new_emails:
                processed_ids.add(email_data["id"])
                _save_processed_email_ids(processed_ids)

                print(
                    "\nNew email received from "
                    f"{email_data.get('sender')} - Subject: {email_data.get('subject')}"
                    f" (is_read={email_data.get('is_read')})"
                )
                print("\n--- Incoming email body ---")
                print(email_data.get("body", "") or "")
                print("--- End incoming email body ---\n")

                attachment_context = ""
                marketing_messages = []
                if email_data.get("has_attachments"):
                    attachment_context, marketing_messages = handle_attachments(
                        graph, email_data
                    )
                else:
                    print("   No attachments.")

                if marketing_messages:
                    draft = "\n\n".join(message for message in marketing_messages if message)
                else:
                    print("Fetching conversation history...")
                    history = graph.fetch_conversation_history(
                        email_data.get("conversation_id")
                    )

                    print("Generating AI draft...")
                    draft = ai_agent.generate_draft(
                        email_data,
                        history=history,
                        attachment_context=attachment_context,
                    )

                print("\n=== AI Generated Draft (full) ===")
                print(draft or "")
                print("=== End Draft ===\n")

                enqueue_approval(email_data, draft)

        except KeyboardInterrupt:
            print("\nShutting down AI Email Agent...")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")

        try:
            messages = onedrive_agent.poll_folder(graph, MARKETING_FOLDER_PATH)
            for message in messages:
                linkedin_message = message.get("linkedin_message", "")
                print("\nGenerated LinkedIn campaign message from OneDrive:")
                print(linkedin_message)
                if linkedin_message:
                    send_dm.send_dm(linkedin_message)
        except Exception as e:
            print(f"Error in OneDrive polling loop: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
