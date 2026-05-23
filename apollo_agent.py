import os
import json
import logging
import openai
from datetime import datetime, timezone
from apify_client import ApifyClient
from config import OPENAI_API_KEY

# Set OpenAI API key
openai.api_key = OPENAI_API_KEY

JSON_FILE = "apollo_contacts.json"
MARKETING_JSON_FILE = "marketing_contacts.json"

# Paid Apollo-alternative actor on Apify. Returns validated work emails,
# personal emails, LinkedIn URLs, company info — all with structured fields
# (no truncated "..." like Google snippets). Override via APIFY_LEADS_ACTOR.
APIFY_ACTOR = os.getenv("APIFY_LEADS_ACTOR", "code_crafter/leads-finder")

log_formatter = logging.Formatter("%(asctime)s  %(levelname)s  [Apollo CRM] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

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

_USA_VARIANTS = {"united states", "us", "usa", "united states of america"}

def _is_usa(country: str) -> bool:
    return (country or "").strip().lower() in _USA_VARIANTS

def get_apify_api_token():
    """Retrieve Apify API token from env."""
    return os.getenv("APIFY_API_TOKEN", "")

def get_apollo_api_key():
    """Backwards-compatible alias used by the UI's `has_key` check.
    Now returns the Apify token (the active data source)."""
    return get_apify_api_token()


def _first(item: dict, *keys: str) -> str:
    """First non-empty string value among the given top-level keys."""
    for k in keys:
        v = item.get(k)
        if v is None:
            continue
        if isinstance(v, dict):
            continue
        s = str(v).strip()
        if s and s.lower() not in ("none", "null", "n/a"):
            return s
    return ""


def _nested(item: dict, parent: str, *keys: str) -> str:
    obj = item.get(parent)
    if not isinstance(obj, dict):
        return ""
    return _first(obj, *keys)


def _normalise_apify_record(item: dict) -> dict:
    """Map a leads-finder dataset record to our CRM contact schema.
    Robust to field-name variations across actor versions."""

    name = _first(item, "name", "full_name", "fullName", "contact_name")
    if not name:
        first = _first(item, "first_name", "firstName")
        last  = _first(item, "last_name", "lastName")
        name  = f"{first} {last}".strip()

    title = _first(item,
                   "title", "job_title", "jobTitle",
                   "contact_title", "headline", "position")

    email = _first(item,
                   "email", "work_email", "workEmail",
                   "business_email", "businessEmail",
                   "personal_email", "personalEmail",
                   "contact_email")

    company = _first(item,
                     "company", "company_name", "companyName",
                     "organization", "organization_name", "organizationName",
                     "employer")
    if not company:
        company = _nested(item, "organization", "name", "company_name")
    if not company:
        company = _nested(item, "company", "name")

    industry = _first(item, "industry", "company_industry", "companyIndustry")
    if not industry:
        industry = _nested(item, "organization", "industry") \
                   or _nested(item, "company", "industry")
    if not industry:
        industry = "Hospital & Health Care"

    city = _first(item, "city", "contact_city", "location_city",
                  "person_city", "present_city")
    if not city:
        city = _nested(item, "location", "city") or _nested(item, "address", "city")

    country = _first(item, "country", "contact_country", "location_country", "person_country")
    if not country:
        country = _nested(item, "location", "country") or _nested(item, "address", "country")
    if not country:
        country = "United States"

    linkedin = _first(item,
                      "linkedin", "linkedin_url", "linkedinUrl",
                      "linkedinProfile", "linkedin_profile",
                      "contact_linkedin_url")
    if not linkedin:
        linkedin = _nested(item, "social", "linkedin")

    return {
        "name":     name,
        "title":    title,
        "email":    (email or "").strip(),
        "company":  company,
        "industry": industry or "Hospital & Health Care",
        "city":     city,
        "country":  country or "United States",
        "linkedin": linkedin,
    }


def _run_apify_leads(fetch_count: int, keywords: list[str], roles: list[str]) -> list[dict]:
    """Call the leads-finder Apify actor once with the given filters.
    Returns the raw dataset items (callers normalise + filter)."""
    token = get_apify_api_token()
    if not token:
        log.error("APIFY_API_TOKEN is missing — cannot fetch leads")
        return []

    client = ApifyClient(token)

    run_input = {
        "fetch_count":        fetch_count,
        "file_name":          "USA Hospital Pharmacy Contacts",
        "contact_job_title":  roles,
        "contact_location":   ["united states"],
        "company_industry":   [
            "hospital & health care",
            "pharmaceuticals",
            "medical devices",
            "biotechnology",
            "medical practice",
        ],
        "company_keywords":   keywords,
        "email_status":       ["validated"],
    }

    log.info(f"Calling Apify actor {APIFY_ACTOR} — fetch_count={fetch_count}, "
             f"{len(keywords)} keyword(s), {len(roles)} role(s)")

    try:
        run = client.actor(APIFY_ACTOR).call(run_input=run_input)
    except Exception as e:
        log.error(f"Apify actor call failed: {e}")
        return []

    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        log.error("Apify run did not return a dataset id")
        return []

    log.info(f"Apify actor finished. Reading dataset {dataset_id} …")
    items = list(client.dataset(dataset_id).iterate_items())
    log.info(f"Dataset contains {len(items)} raw lead records")
    return items

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


def load_marketing_contacts() -> list[dict]:
    """Load Marketing-Agent-specific contacts from a separate JSON file."""
    if os.path.exists(MARKETING_JSON_FILE):
        try:
            with open(MARKETING_JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Error loading {MARKETING_JSON_FILE}: {e}")
    return []


def save_marketing_contacts(contacts: list[dict]):
    """Save Marketing-Agent-specific contacts to a separate JSON file."""
    try:
        with open(MARKETING_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(contacts, f, indent=2, ensure_ascii=False)
        log.info(f"Saved {len(contacts)} marketing contacts to {MARKETING_JSON_FILE}")
    except Exception as e:
        log.error(f"Error saving to {MARKETING_JSON_FILE}: {e}")


def get_sent_campaign_emails() -> dict[str, dict]:
    """Return mapping of {lowercased_email: contact} for every Apollo CRM contact
    whose status is 'Sent' or 'Replied'. The Pipeline uses this to recognise
    inbound mail as a campaign reply (Sent → first reply, Replied → follow-up)."""
    contacts = load_contacts()
    return {
        c["email"].lower(): c
        for c in contacts
        if c.get("status") in ("Sent", "Replied") and c.get("email")
    }


def _normalize_linkedin_url(url: str) -> str:
    """LinkedIn profile URLs come in a few flavours (with/without trailing slash,
    http vs https, www vs no-www). Normalise to a single canonical form so the
    DM Pipeline filter matches reliably."""
    s = (url or "").strip().lower().rstrip("/")
    s = s.replace("http://", "https://")
    s = s.replace("https://linkedin.com/", "https://www.linkedin.com/")
    return s


def get_sent_dm_recipients() -> dict[str, dict]:
    """Mirror of get_sent_campaign_emails() but for LinkedIn. Returns
    {normalized_linkedin_url: contact} for every marketing_contacts.json entry
    we've DM'd ('Sent' or 'Replied' status).  Used by the LinkedIn Pipeline
    filter to recognise incoming DMs as campaign replies."""
    contacts = load_marketing_contacts()
    out = {}
    for c in contacts:
        url = c.get("linkedin")
        if not url:
            continue
        if c.get("status") not in ("Sent", "Replied"):
            continue
        out[_normalize_linkedin_url(url)] = c
    return out


def _name_key(name: str) -> str:
    """Normalise a person name for fuzzy matching: lowercase, strip degree
    suffixes like ', PharmD' or ', MBA', collapse whitespace.  LinkedIn often
    shows the same person as 'Jane Smith' vs 'Jane Smith, PharmD' vs
    'Jane Smith, MBA' — we treat all three as the same person."""
    s = (name or "").lower().strip()
    # Drop everything after the first comma (degrees, certifications, titles)
    if "," in s:
        s = s.split(",", 1)[0].strip()
    # Collapse whitespace
    s = " ".join(s.split())
    return s


def match_marketing_contact(profile_url: str = "", name: str = "") -> dict | None:
    """Find a contact in marketing_contacts.json matching the given LinkedIn
    profile URL OR display name. Used by the LinkedIn Pipeline because the
    URL LinkedIn shows in messaging is the URN form (e.g. /in/ACoAA...)
    while our stored URL is the vanity form (/in/muhammad-haris-2a805b24b/).
    The URLs don't string-match, so we fall back to name matching when needed.

    Returns the first matched contact (must have status in Sent/Replied) or
    None. Caller is responsible for any further filtering."""
    contacts = load_marketing_contacts()
    url_needle = _normalize_linkedin_url(profile_url) if profile_url else ""
    name_needle = _name_key(name)

    for c in contacts:
        if c.get("status") not in ("Sent", "Replied"):
            continue
        if url_needle and _normalize_linkedin_url(c.get("linkedin") or "") == url_needle:
            return c

    # No URL match — fall back to name (case + degree-suffix insensitive)
    if name_needle:
        for c in contacts:
            if c.get("status") not in ("Sent", "Replied"):
                continue
            if _name_key(c.get("name") or "") == name_needle:
                return c

    return None


def mark_marketing_contact_replied(linkedin_url: str) -> dict | None:
    """When an inbound LinkedIn DM matches a sent-campaign contact, flip their
    status to 'Replied'. Returns the matched contact so the caller can pull
    the original DM (stored in c['draft']) for the approval card. Idempotent."""
    contacts = load_marketing_contacts()
    needle = _normalize_linkedin_url(linkedin_url)
    matched = None
    for c in contacts:
        if _normalize_linkedin_url(c.get("linkedin") or "") != needle:
            continue
        if c.get("status") not in ("Sent", "Replied"):
            continue
        matched = c
        c["status"] = "Replied"
        c["replied_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        break
    if matched:
        save_marketing_contacts(contacts)
        log.info(f"Marketing contact {linkedin_url} marked as Replied")
    return matched


def mark_contact_replied(email: str) -> dict | None:
    """When an inbound email matches a sent-campaign contact, flip their status
    to 'Replied' so the Apollo CRM tab shows who's responded. Returns the
    matched contact (post-update) so the caller can pull the original campaign
    subject/body for context. Idempotent: a contact already in 'Replied' is
    just touched with a fresh timestamp."""
    contacts = load_contacts()
    needle = (email or "").lower()
    matched = None
    for c in contacts:
        if c.get("email", "").lower() != needle:
            continue
        if c.get("status") not in ("Sent", "Replied"):
            continue
        matched = c
        c["status"] = "Replied"
        c["replied_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        break
    if matched:
        save_contacts(contacts)
        log.info(f"Contact {email} marked as Replied")
    return matched

def fetch_apollo_leads(fetch_count: int = 100, keyword: str = "hospital", roles: list[str] = None) -> str:
    """
    Fetch leads via the Apify leads-finder actor and merge into apollo_contacts.json.
    (Name kept for backwards-compatibility with the UI; data source is Apify, not Apollo.)
    """
    if not get_apify_api_token():
        return "APIFY_API_TOKEN is missing. Please add it to your .env file or configuration."

    try:
        fetch_count = int(fetch_count)
    except (TypeError, ValueError):
        fetch_count = 100
    if fetch_count < 1:
        fetch_count = 1

    if not keyword:
        keyword = "hospital"
    if not roles:
        roles = TARGET_TITLES

    if isinstance(keyword, list):
        keywords = [str(k).strip() for k in keyword if str(k).strip()]
    else:
        keywords = [k.strip() for k in str(keyword).split(",") if k.strip()]
    if not keywords:
        keywords = ["hospital"]

    raw = _run_apify_leads(fetch_count=fetch_count, keywords=keywords, roles=roles)
    if not raw:
        return "No leads returned by Apify for these filters."

    # Normalise → require email → USA filter
    enriched_contacts = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for item in raw:
        row = _normalise_apify_record(item)
        if not row["email"]:
            continue
        # Drop "..." truncated company names just in case.
        company = row["company"] or ""
        if company.endswith("...") or company.startswith("..."):
            continue
        enriched_contacts.append({
            "id":         row["email"],
            "name":       row["name"],
            "title":      row["title"],
            "email":      row["email"],
            "company":    row["company"],
            "industry":   row["industry"],
            "city":       row["city"],
            "country":    row["country"],
            "linkedin":   row["linkedin"],
            "fetched_at": ts,
            "status":     "Lead Fetched",
            "subject":    None,
            "draft":      None,
        })

    before_filter = len(enriched_contacts)
    enriched_contacts = [c for c in enriched_contacts if _is_usa(c.get("country", ""))]
    log.info(f"USA filter: kept {len(enriched_contacts)}/{before_filter} enriched contacts")

    # Merge into existing apollo_contacts.json, preserving any draft/sent state.
    existing = load_contacts()
    existing_by_email = {c["email"]: c for c in existing}

    added_count = 0
    updated_count = 0

    for c in enriched_contacts:
        email = c["email"]
        if email in existing_by_email:
            existing_c = existing_by_email[email]
            existing_c.update({
                "name":     c["name"],
                "title":    c["title"],
                "company":  c["company"],
                "industry": c["industry"],
                "city":     c["city"],
                "country":  c["country"],
                "linkedin": c["linkedin"],
            })
            updated_count += 1
        else:
            existing.append(c)
            added_count += 1

    save_contacts(existing)
    log.info(f"Fetch complete — added {added_count} new, updated {updated_count} existing.")
    return (f"Fetched {added_count + updated_count} leads ({added_count} new, "
            f"{updated_count} updated). Review the preview and click Send to dispatch campaigns.")


def send_campaign_to_all(on_log=None) -> dict:
    """
    Generate a personalised campaign email for every unsent lead and send via Graph API.
    Calls on_log({"msg": str, "level": "info"|"ok"|"error"}) for live UI streaming.
    """
    from graph import Graph
    from config import CLIENT_ID, AUTHORITY, GRAPH_SCOPES

    def emit(msg, level="info"):
        log.info(msg)
        if on_log:
            on_log({"msg": msg, "level": level})

    contacts = load_contacts()
    targets = [c for c in contacts if c.get("status") not in ("Sent", "Discarded") and c.get("email")]
    total = len(targets)

    if total == 0:
        emit("No leads to send — all contacts are already sent or discarded.", "info")
        return {"total": 0, "sent": 0, "failed": 0}

    emit(f"Starting campaign send for {total} lead(s)…")

    try:
        graph_client = Graph(CLIENT_ID, AUTHORITY, GRAPH_SCOPES)
    except Exception as e:
        emit(f"Failed to initialise Graph API: {e}", "error")
        return {"error": str(e)}

    sent_count = 0
    fail_count = 0

    for i, lead in enumerate(targets):
        email = lead["email"]
        emit(f"[{i+1}/{total}] Generating draft for {email}…")

        res = generate_campaign_draft(email)
        if "error" in res:
            emit(f"[{i+1}/{total}] Draft failed for {email}: {res['error']}", "error")
            fail_count += 1
            continue

        subject = res.get("subject")
        body = res.get("body") or res.get("draft")
        if not subject or not body:
            emit(f"[{i+1}/{total}] Empty draft for {email}", "error")
            fail_count += 1
            continue

        emit(f'[{i+1}/{total}] Sending to {email} — "{subject[:55]}"…')
        try:
            success = graph_client.send_new_email(email, subject, body)
            if success:
                fresh = load_contacts()
                ref = next((c for c in fresh if c["email"] == email), None)
                if ref:
                    ref["status"] = "Sent"
                    ref["subject"] = subject
                    ref["draft"] = body
                    save_contacts(fresh)
                emit(f"[{i+1}/{total}] Sent OK → {email}", "ok")
                sent_count += 1
            else:
                emit(f"[{i+1}/{total}] Graph API returned failure for {email}", "error")
                fail_count += 1
        except Exception as e:
            emit(f"[{i+1}/{total}] Exception for {email}: {e}", "error")
            fail_count += 1

    emit(f"Campaign complete — Sent: {sent_count}, Failed: {fail_count}", "ok")
    return {"total": total, "sent": sent_count, "failed": fail_count}


def fetch_linkedin_contacts(fetch_count: int = 10, keyword: str = "hospital", roles: list[str] = None) -> list[dict]:
    """
    Fetch leads via Apify for the Marketing Agent and save to marketing_contacts.json
    (kept separate from the Apollo CRM apollo_contacts.json).
    Returns contacts that have a LinkedIn profile URL.
    """
    if not get_apify_api_token():
        log.error("APIFY_API_TOKEN missing — cannot fetch LinkedIn contacts")
        return []

    try:
        fetch_count = int(fetch_count)
    except (TypeError, ValueError):
        fetch_count = 10
    if fetch_count < 1:
        fetch_count = 1

    if not keyword:
        keyword = "hospital"
    if not roles:
        roles = TARGET_TITLES

    if isinstance(keyword, list):
        keywords = [str(k).strip() for k in keyword if str(k).strip()]
    else:
        keywords = [k.strip() for k in str(keyword).split(",") if k.strip()]
    if not keywords:
        keywords = ["hospital"]

    raw = _run_apify_leads(fetch_count=fetch_count, keywords=keywords, roles=roles)
    if not raw:
        log.warning("Apify returned no leads for LinkedIn fetch")
        return []

    enriched = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for item in raw:
        row = _normalise_apify_record(item)
        if not row["linkedin"]:
            continue
        enriched.append({
            "id":         row["email"] or row["linkedin"],
            "name":       row["name"],
            "title":      row["title"],
            "email":      row["email"],
            "company":    row["company"],
            "industry":   row["industry"],
            "city":       row["city"],
            "country":    row["country"],
            "linkedin":   row["linkedin"],
            "fetched_at": ts,
            "status":     "Lead Fetched",
            "subject":    None,
            "draft":      None,
        })

    before_filter = len(enriched)
    enriched = [c for c in enriched if _is_usa(c.get("country", ""))]
    log.info(f"USA filter: kept {len(enriched)}/{before_filter} LinkedIn contacts")

    existing = load_marketing_contacts()
    existing_ids = {c.get("id") for c in existing if c.get("id")}
    added = 0
    for c in enriched:
        if c["id"] not in existing_ids:
            existing.append(c)
            added += 1
    save_marketing_contacts(existing)
    log.info(f"fetch_linkedin_contacts: {added} new contacts added to {MARKETING_JSON_FILE}, "
             f"{len(enriched)} fetched with LinkedIn this run")
    return enriched

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
