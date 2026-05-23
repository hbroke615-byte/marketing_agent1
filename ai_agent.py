import openai
from config import OPENAI_API_KEY

openai.api_key = OPENAI_API_KEY

def generate_draft(email_data, feedback=None, history=None, attachment_context=None):
    sender = email_data.get("sender", "Unknown")
    subject = email_data.get("subject", "No Subject")
    body = email_data.get("body", "")

    system_prompt = """You are a highly professional AI Email Assistant for a leading Fintech company.
Your job is to draft polite, trustworthy, and compliant replies to incoming emails. 

STRICT FINTECH GUARDRAILS:
1. NO LEAKING PII: Never include or repeat Personally Identifiable Information (SSN, credit card numbers, passwords, etc.). Redact any PII if you must reference it.
2. NO RISKY FINANCIAL ADVICE: Do not give explicit investment, trading, or financial advice. Advise the user to consult a certified financial planner if they ask for advice.
3. TONE: Your tone must be formal, polite, highly professional, empathetic, and instill trust.
4. REGULATORY METRICS: Ensure language complies with general financial regulations (e.g. do not guarantee returns, do not promise risk-free investments).
5. NO HALLUCINATIONS: Do not make up company policies, facts, or numbers. If you do not know the answer, politely state that a specialized team member will review their request.
6. NO PLACEHOLDERS: NEVER use placeholders like [Your Name], [Your Position], [Company Name], or [Contact Information]. Do NOT use any brackets []. The email must be ready to send immediately. End with "Best regards," and NOTHING ELSE.
"""
    
    user_prompt = f"""
Incoming Email Details:
Sender: {sender}
Subject: {subject}
Body:
{body}
"""

    if attachment_context and attachment_context.strip():
        user_prompt += "\n--- ATTACHMENT CONTENT ---\n"
        user_prompt += attachment_context.strip()[:30000]  # cap at 30K chars for safety
        user_prompt += "\n--- END ATTACHMENT CONTENT ---\n\n"

    if history:
        user_prompt += "\n--- CONVERSATION HISTORY ---\n"
        for i, old_email in enumerate(history):
            user_prompt += f"Email {i+1} - From: {old_email.get('sender')} | Subject: {old_email.get('subject')}\nBody:\n{old_email.get('body')}\n\n"
        user_prompt += "--- END CONVERSATION HISTORY ---\n\n"

    user_prompt += "Please draft a professional and compliant reply to this email conforming strictly to the guardrails above. Use the attachment content and conversation history if present to make the reply as relevant and accurate as possible. Do not include subject lines or headers in your draft, just the body of the reply.\n"

    if feedback:
        user_prompt += f"\n\nThe user provided the following feedback for the previous draft you generated. Please incorporate this feedback into the new draft:\nFeedback: {feedback}"

    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=600
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error generating AI draft: {e}")
        return f"[Error generating draft: {e}]"


# ───────────────────────────────────────────────────────────────────────────
# LinkedIn DM reply drafting
# ───────────────────────────────────────────────────────────────────────────

LINKEDIN_REPLY_SYSTEM_PROMPT = """You are a senior B2B sales and marketing
representative at Galaxy Pharma replying to LinkedIn direct messages on behalf
of Dalbir Bains.

Write a single LinkedIn DM reply to the most recent incoming message. The reply
will be sent as-is — no human editing.

STRICT RULES (non-negotiable):
1. LinkedIn DM tone: short, warm, conversational. 2–5 sentences max. No
   formal "Dear …" opening, no formal "Best regards, …" sign-off.
2. NEVER use bracketed placeholders. Forbidden: [Your Name], [Name],
   [Your Company], [Company Name], [Your Position], [Title], [Insert ...],
   [Date], [Recipient], [Contact]. If you can't determine a real fact, omit
   the sentence.
3. Do NOT add a name signature at the end. LinkedIn already shows who sent
   the message.
4. No subject lines. No email-style headers. Just the message text.
5. If the person asked a specific question, answer it directly. If they
   declined or unsubscribed, respond politely and don't push.
6. Use the full conversation history (the original campaign DM and any
   replies that followed) so your response is informed by what was already
   said. Don't repeat yourself.
"""


def generate_linkedin_reply(
    incoming_message: str,
    conversation_history: list[dict] | None = None,
    contact_metadata: dict | None = None,
    original_campaign: str | None = None,
) -> str:
    """Draft a LinkedIn DM reply.

    incoming_message      — the latest message we received (the one to reply to).
    conversation_history  — list of {sender, text, ts} from linkedin_inbox.py,
                            chronological. Includes the original campaign + any
                            back-and-forth. Used for full context.
    contact_metadata      — optional {"name", "title", "company", "industry",
                            "city", "country"} from marketing_contacts.json.
    original_campaign     — optional. The original DM we sent (also in history,
                            but the caller may want to highlight it).
    """
    parts = ["Recipient profile:"]
    if contact_metadata:
        for k in ("name", "title", "company", "industry", "city", "country"):
            v = contact_metadata.get(k)
            if v:
                parts.append(f"  {k}: {v}")

    if original_campaign:
        parts.append("\nOriginal campaign DM we sent them:")
        parts.append(original_campaign.strip())

    if conversation_history:
        parts.append("\nFull conversation so far (chronological):")
        for i, m in enumerate(conversation_history, 1):
            sender = m.get("sender") or "?"
            text = (m.get("text") or "").strip()
            ts = m.get("ts") or ""
            ts_suffix = f"  ({ts})" if ts else ""
            parts.append(f"  [{i}] {sender}{ts_suffix}: {text}")

    parts.append("\nMost recent incoming message to reply to:")
    parts.append(incoming_message.strip())
    parts.append("\nDraft the LinkedIn DM reply now. Output the reply text only.")

    user_prompt = "\n".join(parts)

    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": LINKEDIN_REPLY_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.6,
            max_tokens=400,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error generating LinkedIn AI reply: {e}")
        return f"[Error generating LinkedIn reply: {e}]"
