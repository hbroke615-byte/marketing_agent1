import time

import ai_agent
import apollo_agent
import linkedin_inbox
import marketing_campaign_agent
import onedrive_agent
import send_dm
from approval_ui import (
    enqueue_approval,
    enqueue_linkedin_approval,
    ensure_approval_server,
    linkedin_queue_has_message_id,
    pop_completed_approvals,
    pop_completed_linkedin_approvals,
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

PROCESSED_EMAILS_JSON           = "processed_email_ids.json"
PROCESSED_LINKEDIN_IDS_JSON     = "processed_linkedin_message_ids.json"

# LinkedIn polling is much heavier than Outlook (launches headless Chromium,
# navigates LinkedIn's web UI, scrapes the DOM). We can't poll every 30 sec
# like email. This counter throttles it to roughly every 10 minutes regardless
# of POLL_INTERVAL.
LINKEDIN_POLL_EVERY_SECONDS = 600  # 10 minutes


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


def _load_processed_linkedin_ids():
    try:
        import json
        import os
        if os.path.exists(PROCESSED_LINKEDIN_IDS_JSON):
            with open(PROCESSED_LINKEDIN_IDS_JSON, "r", encoding="utf-8") as f:
                return set(json.load(f) or [])
    except Exception as e:
        print(f"Could not load processed LinkedIn IDs: {e}")
    return set()


def _save_processed_linkedin_ids(processed_ids):
    try:
        import json
        with open(PROCESSED_LINKEDIN_IDS_JSON, "w", encoding="utf-8") as f:
            json.dump(sorted(processed_ids), f)
    except Exception as e:
        print(f"Could not save processed LinkedIn IDs: {e}")


def poll_linkedin_inbox(processed_li_ids):
    """One scrape-and-queue pass for incoming LinkedIn DMs. Only DMs from
    people in marketing_contacts.json with status in ('Sent', 'Replied')
    end up in the approval queue."""
    print("\n📩 Polling LinkedIn inbox (this may take 15–60 sec)…")

    recipients = apollo_agent.get_sent_dm_recipients()
    if not recipients:
        print("   No DM recipients tracked (marketing_contacts.json has no 'Sent' status). Skipping scrape.")
        return processed_li_ids

    try:
        conversations = linkedin_inbox.fetch_recent_conversations(max_conversations=10)
    except Exception as e:
        print(f"   LinkedIn scrape failed: {e}")
        return processed_li_ids

    if not conversations:
        print("   LinkedIn scrape returned no conversations.")
        return processed_li_ids

    new_queued = 0
    ignored = 0

    for convo in conversations:
        other_url    = convo.get("other_profile_url") or ""
        other_name   = convo.get("other_name") or ""
        latest_id    = convo.get("latest_id") or ""
        latest_text  = (convo.get("latest_text") or "").strip()
        latest_sender = convo.get("latest_sender") or other_name

        if not latest_id or not latest_text:
            continue

        # Filter 1: must be a known recipient — match by URL or by name.
        # LinkedIn's messaging UI gives us URN-form URLs (/in/ACoAA...) while
        # our marketing_contacts.json stores vanity URLs (/in/muhammad-haris-...).
        # The name fallback bridges the gap.
        contact = apollo_agent.match_marketing_contact(profile_url=other_url, name=other_name)
        if not contact:
            ignored += 1
            continue

        # Filter 2: dedupe — already queued or already processed
        if latest_id in processed_li_ids or linkedin_queue_has_message_id(latest_id):
            continue

        # Filter 3 (heuristic): skip if the latest message came from US.
        # LinkedIn shows "You" as the sender label on outgoing messages.
        # No point drafting a reply when we're the one who spoke last.
        if latest_sender and latest_sender.lower().startswith("you"):
            print(f"   Skipping {other_name!r}: latest message was from us.")
            continue

        contact_url = contact.get("linkedin") or other_url
        print(f"   New DM from {contact.get('name')!r} ({contact_url})")
        print(f"      Latest: {latest_text[:120]!r}")

        # Mark contact as Replied (idempotent) — use the contact's stored URL
        # so the status flip lands on the right row.
        apollo_agent.mark_marketing_contact_replied(contact_url)

        # Generate AI reply with full thread context
        print("   Generating AI reply with full thread context…")
        draft = ai_agent.generate_linkedin_reply(
            incoming_message=latest_text,
            conversation_history=convo.get("messages") or [],
            contact_metadata=contact,
            original_campaign=contact.get("draft"),  # the DM we originally sent
        )
        print(f"   AI draft: {draft[:140]!r}")

        original_campaign = {
            "body":    contact.get("draft") or "",
            "name":    contact.get("name") or "",
            "company": contact.get("company") or "",
        }

        enqueue_linkedin_approval(convo, draft, original_campaign=original_campaign)
        processed_li_ids.add(latest_id)
        new_queued += 1

    if new_queued:
        _save_processed_linkedin_ids(processed_li_ids)
    print(f"   LinkedIn poll done: {new_queued} new DM(s) queued, {ignored} ignored (not in recipients).")
    return processed_li_ids


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

    processed_ids    = _load_processed_email_ids()
    processed_li_ids = _load_processed_linkedin_ids()
    last_linkedin_poll = 0.0
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

            # ── Approved LinkedIn replies → send via Playwright (send_dm) ──
            for action in pop_completed_linkedin_approvals():
                li_data = action["linkedin_data"] or {}
                profile_url = li_data.get("other_profile_url")
                other_name  = li_data.get("other_name") or "Unknown"
                if action["approved"] and action.get("final_draft") and profile_url:
                    print(f"Sending approved LinkedIn DM to {other_name} ({profile_url})…")
                    try:
                        ok = send_dm.send_dm(action["final_draft"], profile_url=profile_url)
                    except Exception as e:
                        print(f"  LinkedIn DM send raised: {e}")
                        ok = False
                    if ok:
                        print("  LinkedIn DM sent.")
                    else:
                        print("  LinkedIn DM send returned failure.")
                else:
                    print(f"LinkedIn approval cancelled for {other_name}.")

            print("\nChecking Inbox for recent emails...")
            recent_emails = graph.fetch_recent_emails(top=10)
            print(f"Fetched {len(recent_emails)} recent message(s).")
            unread_emails = graph.fetch_unread_emails()
            update_inbox_stats(len(unread_emails), len(recent_emails))

            # Pipeline scope: only emails from people we've sent an Apollo CRM
            # campaign to. Everything else (Stripe receipts, marketing blasts,
            # personal mail) is ignored — left untouched in the Inbox.
            campaign_recipients = apollo_agent.get_sent_campaign_emails()
            print(
                f"Pipeline scope: {len(campaign_recipients)} campaign recipient(s)"
                " — only replies from these addresses will be drafted."
            )

            new_emails = [
                email
                for email in recent_emails
                if email.get("id")
                and email.get("id") not in processed_ids
                and (email.get("sender") or "").lower() in campaign_recipients
            ]

            ignored = [
                e for e in recent_emails
                if e.get("id")
                and e.get("id") not in processed_ids
                and (e.get("sender") or "").lower() not in campaign_recipients
            ]
            if ignored:
                print(
                    f"Ignoring {len(ignored)} non-campaign email(s)"
                    f" (e.g. {ignored[0].get('sender')!r})."
                )

            if not new_emails:
                print("No new campaign replies to draft.")

            for email_data in new_emails:
                processed_ids.add(email_data["id"])
                _save_processed_email_ids(processed_ids)

                sender_email = (email_data.get("sender") or "").lower()
                campaign_contact = apollo_agent.mark_contact_replied(sender_email)

                print(
                    "\n📨 Campaign reply from "
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
                    print("Fetching full conversation history (no cap)...")
                    history = graph.fetch_conversation_history(
                        email_data.get("conversation_id")
                    )
                    print(f"   Got {len(history)} message(s) in thread.")

                    print("Generating AI draft with full thread context...")
                    draft = ai_agent.generate_draft(
                        email_data,
                        history=history,
                        attachment_context=attachment_context,
                    )

                print("\n=== AI Generated Draft (full) ===")
                print(draft or "")
                print("=== End Draft ===\n")

                # Build the "original campaign" panel data for the approval card.
                original_campaign = None
                if campaign_contact:
                    original_campaign = {
                        "subject": campaign_contact.get("subject") or "",
                        "body":    campaign_contact.get("draft") or "",
                        "name":    campaign_contact.get("name") or "",
                        "company": campaign_contact.get("company") or "",
                    }

                enqueue_approval(email_data, draft, original_campaign=original_campaign)

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

        # ── Throttled LinkedIn inbox scrape (every ~10 min, not every iter) ──
        now = time.time()
        if now - last_linkedin_poll >= LINKEDIN_POLL_EVERY_SECONDS:
            try:
                processed_li_ids = poll_linkedin_inbox(processed_li_ids)
            except Exception as e:
                print(f"Error in LinkedIn polling: {e}")
            last_linkedin_poll = now

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
