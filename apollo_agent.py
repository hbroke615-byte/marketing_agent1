import os
import json
import logging
import requests
import openai
from datetime import datetime
from config import OPENAI_API_KEY

# Set OpenAI API key
openai.api_key = OPENAI_API_KEY

JSON_FILE = "apollo_contacts.json"

log_formatter = logging.Formatter("%(asctime)s  %(levelname)s  [CRM Agent] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

file_handler = logging.FileHandler("apollo_crm.log", encoding="utf-8")
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

log = logging.getLogger("apollo_crm")
log.setLevel(logging.INFO)
log.propagate = False
log.addHandler(file_handler)
log.addHandler(console_handler)


TARGET_TITLES = [
    "procurement manager",
    "business development",
    "supply chain manager",
    "purchasing manager",
    "sourcing manager",
]

def get_apollo_api_key():
    """Retrieve Apollo API Key from env."""
    return os.getenv("APOLLO_API_KEY", "")

def load_contacts() -> list[dict]:
    """Load contacts from nested JSON file."""
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Error loading {JSON_FILE}: {e}")
    return []

def save_contacts(contacts: list[dict]):
    """Save contacts to nested JSON file."""
    try:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(contacts, f, indent=2, ensure_ascii=False)
        log.info(f"Saved {len(contacts)} contacts to {JSON_FILE}")
    except Exception as e:
        log.error(f"Error saving to {JSON_FILE}: {e}")

def fetch_apollo_leads(per_page: int = 25, max_pages: int = 1, keyword: str = "pharmaceutical", roles: list[str] = None) -> str:
    """
    Fetch leads from Apollo.io API based on keywords and target titles.
    Enriches them to get email and details, then saves/updates apollo_contacts.json.
    """
    api_key = get_apollo_api_key()
    if not api_key:
        return "APOLLO_API_KEY is missing. Please add it to your .env file or configuration."

    if not keyword:
        keyword = "pharmaceutical"
    if not roles:
        roles = TARGET_TITLES

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "accept": "application/json",
        "x-api-key": api_key,
    }

    all_ids = []
    seen_ids = set()

    # Step 1: Search people (keyword: custom)
    for page in range(1, max_pages + 1):
        params = {
            "per_page": per_page,
            "page": page,
            "q_keywords": keyword,
        }
        for title in roles:
            params.setdefault("person_titles[]", [])
            if isinstance(params["person_titles[]"], list):

                params["person_titles[]"].append(title)

        try:
            res = requests.post(
                "https://api.apollo.io/api/v1/mixed_people/api_search",
                headers=headers,
                params=params,
                timeout=25,
            )
            res.raise_for_status()
            data = res.json()
            people = data.get("people", [])
            ids = [p["id"] for p in people if p.get("has_email")]
            log.info(f"Page {page} search: found {len(people)} people, {len(ids)} have emails")
            
            for pid in ids:
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    all_ids.append(pid)
        except Exception as e:
            log.error(f"Search API error on page {page}: {e}")
            break

    if not all_ids:
        return "No leads found matching the target pharmaceutical criteria."

    # Step 2: Enrich — trade IDs for full profiles
    enriched_contacts = []
    chunk_size = 10
    for i in range(0, len(all_ids), chunk_size):
        chunk = all_ids[i : i + chunk_size]
        payload = {
            "reveal_personal_emails": False,
            "details": [{"id": pid} for pid in chunk],
        }

        try:
            res = requests.post(
                "https://api.apollo.io/api/v1/people/bulk_match",
                headers=headers,
                json=payload,
                timeout=25,
            )
            res.raise_for_status()
            matches = res.json().get("matches", [])
            for p in matches:
                email = p.get("email")
                if not email:
                    continue
                org = p.get("organization") or {}
                
                # Check for duplicates or update existing
                enriched_contacts.append({
                    "id": p.get("id"),
                    "name": (p.get("name") or "").strip(),
                    "title": (p.get("title") or "").strip(),
                    "email": email.strip(),
                    "company": (org.get("name") or "").strip(),
                    "industry": (org.get("industry") or "").strip(),
                    "city": (p.get("city") or "").strip(),
                    "country": (p.get("country") or "").strip(),
                    "linkedin": (p.get("linkedin_url") or "").strip(),
                    "fetched_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "Lead Fetched",
                    "subject": None,
                    "draft": None,
                })
            log.info(f"Enriched chunk {i+1}-{i+len(chunk)}: retrieved {len(matches)} profiles")
        except Exception as e:
            log.error(f"Enrichment error on chunk starting at {i}: {e}")

    # Load existing to avoid overwriting or losing state (like approved drafts)
    existing = load_contacts()
    existing_by_email = {c["email"]: c for c in existing}

    added_count = 0
    updated_count = 0
    for c in enriched_contacts:
        email = c["email"]
        if email in existing_by_email:
            # Update core details but keep status/draft if they exist
            existing_c = existing_by_email[email]
            existing_c.update({
                "name": c["name"],
                "title": c["title"],
                "company": c["company"],
                "industry": c["industry"],
                "city": c["city"],
                "country": c["country"],
                "linkedin": c["linkedin"],
            })
            updated_count += 1
        else:
            existing.append(c)
            added_count += 1

    save_contacts(existing)

    # Automatically scan the entire database for any unsent active prospects to dispatch them instantly!
    leads_to_auto_send = [c for c in existing if c.get("status") not in ("Sent", "Discarded")]

    # Human-out-of-loop dynamic campaign generation & mailing via Graph API
    log.info(f"[Auto CRM Send] Initiating direct campaign mailing loop for {len(leads_to_auto_send)} fetched leads...")
    
    from graph import Graph
    from config import CLIENT_ID, AUTHORITY, GRAPH_SCOPES
    
    try:
        graph_client = Graph(CLIENT_ID, AUTHORITY, GRAPH_SCOPES)
    except Exception as ge:
        log.error(f"[Auto CRM Send] Failed to initialize Microsoft Graph API: {ge}")
        graph_client = None

    auto_sent_count = 0
    auto_fail_count = 0

    for lead in leads_to_auto_send:
        email = lead["email"]
        log.info(f"[Auto CRM Send] Auto-generating personalized campaign email for: {email}")
        
        res = generate_campaign_draft(email)
        if "error" in res:
            log.error(f"[Auto CRM Send] Draft generation failed for {email}: {res['error']}")
            auto_fail_count += 1
            continue

        subject = res.get("subject")
        body = res.get("body") or res.get("draft")
        
        if not subject or not body:
            log.error(f"[Auto CRM Send] Missing subject or body in generated draft for {email}")
            auto_fail_count += 1
            continue

        # Reload latest contacts from disk to prevent race conditions or state loss
        existing = load_contacts()
        lead_ref = next((c for c in existing if c["email"] == email), None)
        if not lead_ref:
            log.error(f"[Auto CRM Send] Lead {email} not found in reloaded list")
            auto_fail_count += 1
            continue

        # Save generated email to DB (Intermediate state in case sending fails)
        lead_ref["subject"] = subject
        lead_ref["draft"] = body
        lead_ref["status"] = "Draft Generated"
        save_contacts(existing)

        # Dispatch via Outlook Graph API
        if graph_client:
            try:
                log.info(f"[Auto CRM Send] Sending outbound campaign to: {email} (Subject: '{subject}')")
                success = graph_client.send_new_email(email, subject, body)
                if success:
                    lead_ref["status"] = "Sent"
                    save_contacts(existing)
                    log.info(f"[Auto CRM Send] Campaign sent successfully to: {email}!")
                    auto_sent_count += 1
                else:
                    log.error(f"[Auto CRM Send] Microsoft Graph API returned failure sending email to {email}")
                    auto_fail_count += 1
            except Exception as se:
                log.error(f"[Auto CRM Send] Exception occurred while sending campaign to {email}: {se}")
                auto_fail_count += 1
        else:
            log.warn(f"[Auto CRM Send] Microsoft Graph API is not authenticated. Skipping auto-sending for: {email}")
            auto_fail_count += 1

    return f"Successfully fetched and enriched leads! Added {added_count} new leads, updated {updated_count} existing. Auto-sent: {auto_sent_count}, failed/skipped: {auto_fail_count}."

DEFAULT_SYSTEM_PROMPT = """You are a premium B2B Sales and Marketing Director at Galaxy Pharma.
Your goal is to write a highly personalized, compelling, and warm cold outreach email to pitch high-quality pharmaceutical supply chain, active pharmaceutical ingredient (API) sourcing, contract manufacturing, or distribution solutions.

Highlight Galaxy Pharma's core strengths: high-quality Active Pharmaceutical Ingredients (APIs), GMP-certified facilities, FDA compliance, and robust supply chains that mitigate stockouts.
Use the sender name "Dalbir Bains" (Galaxy Pharma)."""

SYSTEM_PROMPT_RULES = """
You MUST return the output in a strict JSON format with exactly two keys: "subject" and "body". Do not wrap it in markdown code blocks. Example:
{
  "subject": "Sourcing reliability at Pfizer",
  "body": "Dear John,\\n\\n..."
}

CRITICAL RULES:
1. The subject line must be highly professional, click-worthy, and natural.
2. Address the recipient directly using their name.
3. Reference their title, industry, and location if available, to make the email feel natural and highly researched, rather than an automated blast.
4. Keep it warm, elegant, concise, and focused on B2B partnership value.
5. NEVER use brackets, placeholders, or template tags like [Your Name], [Your Position], [Insert Name], [Your Company], or [Date]. The email must be 100% ready to send.
6. End with a polite, low-pressure call-to-action asking for a brief introductory chat or meeting.
7. Always sign off with a professional signature. If no custom sender name or signature details are specified, sign off with "Dalbir Bains" as the sender. Never output generic bracketed placeholders like "[Your Name]" or "[Your Position]" under any circumstances.
"""

PROMPT_FILE = "apollo_system_prompt.txt"

def get_apollo_system_prompt() -> str:
    """Retrieve Apollo system prompt, fallback to default."""
    if os.path.exists(PROMPT_FILE):
        try:
            with open(PROMPT_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    # Self-healing check: if old file contains the technical rules, strip them out
                    if "You MUST return" in content:
                        content = content.split("You MUST return")[0].strip()
                        # Overwrite the file with the clean core description
                        with open(PROMPT_FILE, "w", encoding="utf-8") as fw:
                            fw.write(content)
                    return content
        except Exception as e:
            log.error(f"Error loading {PROMPT_FILE}: {e}")
    return DEFAULT_SYSTEM_PROMPT

def set_apollo_system_prompt(text: str):
    """Save custom Apollo system prompt."""
    try:
        with open(PROMPT_FILE, "w", encoding="utf-8") as f:
            f.write(text.strip())
        log.info(f"Saved custom Apollo system prompt to {PROMPT_FILE}")
    except Exception as e:
        log.error(f"Error saving to {PROMPT_FILE}: {e}")

def generate_campaign_draft(email: str) -> dict:
    """
    Generate a highly personalized pharmaceutical campaign email using OpenAI.
    """
    contacts = load_contacts()
    contact = next((c for c in contacts if c["email"] == email), None)
    if not contact:
        return {"error": f"Lead with email {email} not found in CRM database."}

    # Prompt building: Combine user's core message description with default structural rules
    system_prompt = f"{get_apollo_system_prompt()}\n\n{SYSTEM_PROMPT_RULES}"

    user_prompt = f"""
Please generate the campaign for this pharmaceutical prospect:
Name: {contact.get('name', 'Procurement Officer')}
Title: {contact.get('title', 'Procurement Manager')}
Company: {contact.get('company', 'Pharmaceutical Company')}
Industry: {contact.get('industry', 'Pharmaceuticals')}
Location: {contact.get('city', 'City')}, {contact.get('country', 'Country')}
LinkedIn: {contact.get('linkedin', 'N/A')}
"""

    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=800
        )
        
        content = response.choices[0].message.content.strip()
        
        # Parse JSON content safely
        if content.startswith("```json"):
            content = content.replace("```json", "", 1)
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        data = json.loads(content)
        subject = data.get("subject", "Partnership opportunity with Galaxy Pharma")
        body = data.get("body", "")
        
        # Update CRM state
        contact["subject"] = subject
        contact["draft"] = body
        contact["status"] = "Draft Generated"
        save_contacts(contacts)
        
        return {
            "status": "success",
            "subject": subject,
            "draft": body,
            "contact": contact
        }
    except Exception as e:
        log.error(f"Error generating AI draft for {email}: {e}")
        return {"error": f"Failed to generate campaign: {str(e)}"}
