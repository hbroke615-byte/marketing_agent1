"""LinkedIn messaging inbox scraper.

Why this exists: LinkedIn does NOT offer a public API to read your own DMs on a
personal account. The only way to monitor incoming DMs is to drive the LinkedIn
website with Playwright (same approach send_dm.py already uses for outbound).

What it does:
    fetch_recent_conversations()  → open linkedin.com/messaging/, walk the
                                    conversation list, return a normalized
                                    list of recent threads + their last few
                                    messages.

Caveats (these are real, accept them):
    • LinkedIn's DOM changes every few months → selectors break → expect
      "could not find …" log lines when that happens. The fix is usually a
      one-line selector update in this file.
    • LinkedIn aggressively detects automation from datacenter IPs (Azure).
      Heavy polling may trigger security challenges that lock the account.
    • The session in linkedin_state.json expires ~every 30 days.

We keep this code small and opinionated: best-effort scraping, robust to
missing data, never raises on selector misses (returns empty list instead).
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

LINKEDIN_STORAGE_STATE = "linkedin_state.json"
MESSAGING_URL = "https://www.linkedin.com/messaging/"

# Selectors are listed in order of preference. The scraper tries each one and
# uses the first that finds elements. When LinkedIn changes class names, add
# the new selector at the top of the relevant list and keep the old ones as
# fallbacks. Keep these in code (not config) — they need to ship with the fix.
CONVERSATION_LIST_ITEM_SELECTORS = [
    "li.msg-conversation-listitem",
    "li.msg-conversation-card",
    "li[data-conversation-id]",  # newer LinkedIn DOM
    "div.msg-conversations-container__conversations-list li",
    "ul.msg-conversations-container__conversations-list > li",
]
CONVERSATION_LINK_SELECTORS = [
    "a.msg-conversation-listitem__link",
    "a.msg-conversation-card__link",
    "a[data-control-name='view_message']",
    "a[href*='/messaging/thread/']",
    "div.msg-conversation-card__content",  # newer: clickable div, not anchor
    "div.msg-conversation-listitem__link",
]
UNREAD_BADGE_SELECTORS = [
    ".notification-badge--show",
    ".msg-conversation-card__pill",
    "[data-test-conversation-list-item-unread]",
    ".msg-conversation-card--unread",
]
MESSAGE_BUBBLE_SELECTORS = [
    "li.msg-s-event-listitem",
    "div.msg-s-event-listitem",
    "li.msg-s-message-list__event",
    "[data-event-urn]",  # newer LinkedIn often uses urn-based markup
]
MESSAGE_TEXT_SELECTORS = [
    "p.msg-s-event-listitem__body",
    ".msg-s-event-listitem__body",
    "div.msg-s-event__content",
    "div[dir='auto']",  # very generic fallback; last resort
]
MESSAGE_SENDER_NAME_SELECTORS = [
    ".msg-s-message-group__name",
    ".msg-s-event-listitem__name",
    ".msg-s-message-group__profile-link",
]
MESSAGE_TIMESTAMP_SELECTORS = [
    "time.msg-s-message-group__timestamp",
    "time.msg-s-message-list__time-heading",
    "time",
]
PARTICIPANT_NAME_SELECTORS = [
    "h3.msg-conversation-listitem__participant-names",
    ".msg-conversation-listitem__participant-names",
    ".msg-conversation-card__participant-names",
    "h3.msg-conversation-card__participant-names",
]


def _first_visible(page, selectors: list[str]):
    """Return the first selector from `selectors` that resolves to at least
    one element on the page. Caller probes for visibility/text separately."""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                return sel
        except Exception:
            continue
    return None


def _stable_message_id(thread_url: str, sender: str, text: str, ts: str) -> str:
    """Generate a stable ID for a message we've seen, so we don't re-queue it
    on every poll. We avoid LinkedIn's internal IDs (data-urn) because they
    aren't reliably exposed across LinkedIn's DOM revisions; instead we hash
    (thread, sender, text, ts) which is stable as long as the text/timestamp
    don't change."""
    blob = "|".join([thread_url or "", sender or "", text or "", ts or ""])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _extract_thread_url(page) -> str:
    """The active conversation lives at /messaging/thread/<id>/. The URL is the
    most reliable stable identifier for a thread."""
    try:
        url = page.url
        m = re.search(r"/messaging/thread/([^/?#]+)", url)
        if m:
            return f"https://www.linkedin.com/messaging/thread/{m.group(1)}/"
    except Exception:
        pass
    return ""


def _extract_other_profile_url(page) -> str:
    """The right-hand pane contains a header link to the other party's profile.
    We grab the first /in/<handle> link in that pane."""
    candidate_selectors = [
        "header.msg-overlay-conversation-bubble-header a[href*='/in/']",
        ".msg-thread__link-to-profile",
        ".msg-overlay-bubble-header__details a[href*='/in/']",
        "a[href*='/in/'][data-anchor-send-invite]",
        ".msg-thread a[href*='/in/']",
    ]
    for sel in candidate_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                href = loc.get_attribute("href") or ""
                if "/in/" in href:
                    # Normalise to absolute https://www.linkedin.com URL
                    if href.startswith("/"):
                        href = "https://www.linkedin.com" + href
                    # Strip query string + fragment
                    href = href.split("?")[0].split("#")[0]
                    return href
        except Exception:
            continue
    return ""


def _extract_conversation_messages(page, max_messages: int = 30, debug: bool = False) -> list[dict]:
    """Walk the message bubbles in the currently-open thread (right pane) and
    return them in chronological order. Each message dict has:
        {sender: str, text: str, ts: str}"""
    # Probe every bubble selector and report counts so we can tell which
    # one (if any) matches the current LinkedIn DOM.
    bubble_counts = {}
    for sel in MESSAGE_BUBBLE_SELECTORS:
        try:
            bubble_counts[sel] = page.locator(sel).count()
        except Exception as e:
            bubble_counts[sel] = f"err:{e}"
    if debug:
        print(f"[linkedin_inbox]   bubble selector counts: {bubble_counts}")

    bubble_sel = next(
        (s for s, n in bubble_counts.items() if isinstance(n, int) and n > 0),
        None,
    )
    if not bubble_sel:
        if debug:
            # Dump a small slice of the page HTML so a human can identify
            # the actual selector to add to MESSAGE_BUBBLE_SELECTORS.
            try:
                snippet = page.evaluate(
                    "() => (document.querySelector('main') || document.body).outerHTML.slice(0, 1500)"
                )
                print(f"[linkedin_inbox]   No bubbles matched. First 1500 chars of <main>:\n{snippet}")
            except Exception as e:
                print(f"[linkedin_inbox]   Could not dump HTML: {e}")
        return []

    bubbles = page.locator(bubble_sel)
    n = min(bubbles.count(), max_messages * 3)
    out: list[dict] = []
    last_sender = "?"
    for i in range(n):
        try:
            b = bubbles.nth(i)
            sender = ""
            for ns in MESSAGE_SENDER_NAME_SELECTORS:
                loc = b.locator(ns)
                if loc.count() > 0:
                    sender = (loc.first.inner_text() or "").strip()
                    if sender:
                        break
            if not sender:
                sender = last_sender
            else:
                last_sender = sender

            text = ""
            for ts in MESSAGE_TEXT_SELECTORS:
                loc = b.locator(ts)
                if loc.count() > 0:
                    text = (loc.first.inner_text() or "").strip()
                    if text:
                        break
            if not text:
                continue

            timestamp = ""
            for tts in MESSAGE_TIMESTAMP_SELECTORS:
                loc = b.locator(tts)
                if loc.count() > 0:
                    timestamp = (loc.first.inner_text() or "").strip()
                    if timestamp:
                        break

            out.append({"sender": sender, "text": text, "ts": timestamp})
        except Exception as e:
            if debug:
                print(f"[linkedin_inbox]   bubble #{i} extraction error: {e}")
            continue

    if len(out) > max_messages:
        out = out[-max_messages:]
    return out


def fetch_recent_conversations(
    max_conversations: int = 10,
    max_thread_messages: int = 30,
    headless: bool | None = None,
    debug: bool | None = None,
) -> list[dict]:
    """Open LinkedIn messaging and return up to `max_conversations` recent
    threads. For each thread, return up to `max_thread_messages` messages.

    Returns: list of dicts with shape
        {
            "thread_url":       "https://www.linkedin.com/messaging/thread/abc/",
            "other_profile_url": "https://www.linkedin.com/in/jane-doe-12345/",
            "other_name":       "Jane Doe",
            "messages":         [{sender, text, ts}, ...],   # chronological
            "latest_text":      "their most recent message text",
            "latest_sender":    "Jane Doe",
            "latest_ts":        "Wed 10:23 AM",
            "latest_id":        "<sha1 hash for dedupe>",
            "is_unread":        bool,
        }

    Never raises. Returns [] on any unrecoverable failure (with a print so the
    main loop can log it).
    """
    out: list[dict] = []
    import os
    # Allow LINKEDIN_INBOX_HEADLESS=0 (or DEBUG=1) to force a visible browser
    # window during local debugging without touching code.
    if headless is None:
        headless = os.environ.get("LINKEDIN_INBOX_HEADLESS", "1") != "0"
    # Default to verbose. The scraper is brittle by nature — LinkedIn DOM
    # changes break it ~every few months — so we always want enough log
    # output to diagnose without a redeploy. Override with LINKEDIN_INBOX_DEBUG=0
    # if the logs get noisy once everything is stable.
    if debug is None:
        debug = os.environ.get("LINKEDIN_INBOX_DEBUG", "1") != "0"

    with sync_playwright() as pw:
        browser = None
        try:
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=LINKEDIN_STORAGE_STATE)
            page = context.new_page()

            print(f"[linkedin_inbox] Opening {MESSAGING_URL} …")
            page.goto(MESSAGING_URL, timeout=45000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(5000)

            # Detect "are you sure you're a human" / login challenge pages.
            if "checkpoint" in page.url or "login" in page.url:
                print(f"[linkedin_inbox] Hit a checkpoint/login page: {page.url}. "
                      "Session likely expired or LinkedIn detected automation. Skipping.")
                return []

            list_item_sel = _first_visible(page, CONVERSATION_LIST_ITEM_SELECTORS)
            if not list_item_sel:
                print("[linkedin_inbox] Could not locate the conversation list. "
                      "LinkedIn DOM may have changed — update CONVERSATION_LIST_ITEM_SELECTORS.")
                return []

            items = page.locator(list_item_sel)
            n_items = min(items.count(), max_conversations)
            print(f"[linkedin_inbox] Found {items.count()} conversations, scanning first {n_items}.")

            for i in range(n_items):
                try:
                    item = items.nth(i)

                    unread_sel = _first_visible(item, UNREAD_BADGE_SELECTORS)
                    is_unread = bool(unread_sel)

                    # Grab the participant name from the conversation sidebar
                    # BEFORE clicking. This is the cleanest source for "the
                    # other person's display name" and is what we'll match
                    # against marketing_contacts.json when LinkedIn gives us
                    # a URN-form profile URL instead of the vanity URL.
                    sidebar_name = ""
                    sb_sel = _first_visible(item, PARTICIPANT_NAME_SELECTORS)
                    if sb_sel:
                        try:
                            sidebar_name = (item.locator(sb_sel).first.inner_text() or "").strip()
                        except Exception:
                            sidebar_name = ""

                    link_sel = _first_visible(item, CONVERSATION_LINK_SELECTORS) or "a"
                    link = item.locator(link_sel).first
                    if link.count() == 0:
                        if debug:
                            print(f"[linkedin_inbox]   #{i}: no clickable link, skipping")
                        continue

                    if debug:
                        try:
                            preview = (link.inner_text() or "")[:60].replace("\n", " ")
                        except Exception:
                            preview = "?"
                        print(f"[linkedin_inbox] #{i}: clicking ({preview!r}), unread={is_unread}")

                    # Click the link, then wait for either a URL change (when
                    # LinkedIn full-routes to /messaging/thread/<id>) OR for
                    # message bubbles to actually appear (when LinkedIn opens
                    # the right pane without a URL change). Either path is OK.
                    url_before = page.url
                    link.click(timeout=8000)
                    try:
                        page.wait_for_function(
                            "(args) => window.location.href !== args.url || "
                            "document.querySelectorAll('li.msg-s-event-listitem, div.msg-s-event-listitem').length > 0",
                            arg={"url": url_before},
                            timeout=8000,
                        )
                    except PlaywrightTimeout:
                        if debug:
                            print(f"[linkedin_inbox]   #{i}: navigation/render wait timed out, trying anyway")
                    page.wait_for_timeout(1500)

                    thread_url        = _extract_thread_url(page)
                    other_profile_url = _extract_other_profile_url(page)
                    messages          = _extract_conversation_messages(
                        page, max_messages=max_thread_messages, debug=debug
                    )

                    if debug:
                        print(f"[linkedin_inbox]   #{i}: thread_url={thread_url!r}")
                        print(f"[linkedin_inbox]   #{i}: other_profile_url={other_profile_url!r}")
                        print(f"[linkedin_inbox]   #{i}: messages extracted={len(messages)}")

                    if not messages:
                        continue
                    latest = messages[-1]
                    # Prefer the sidebar name (clean: "Muhammad Haris") over
                    # the latest-message sender (could be "You" if we sent last).
                    other_name = sidebar_name or latest["sender"] or ""

                    latest_id = _stable_message_id(
                        thread_url, latest["sender"], latest["text"], latest["ts"]
                    )

                    out.append({
                        "thread_url":        thread_url,
                        "other_profile_url": other_profile_url,
                        "other_name":        other_name,
                        "messages":          messages,
                        "latest_text":       latest["text"],
                        "latest_sender":     latest["sender"],
                        "latest_ts":         latest["ts"],
                        "latest_id":         latest_id,
                        "is_unread":         is_unread,
                    })

                    if debug:
                        print(f"[linkedin_inbox]   #{i}: sidebar_name={sidebar_name!r}, other_name={other_name!r}")
                except Exception as e:
                    print(f"[linkedin_inbox] Error reading conversation #{i}: {e}")
                    continue

            print(f"[linkedin_inbox] Returned {len(out)} thread(s) with messages.")
            return out

        except PlaywrightTimeout as e:
            print(f"[linkedin_inbox] Playwright timeout: {e}")
            return []
        except Exception as e:
            print(f"[linkedin_inbox] Unrecoverable scrape error: {e}")
            return []
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass


if __name__ == "__main__":
    # Manual test: run this file directly to scrape your own inbox once.
    # Useful for debugging selector breakage without spinning up the whole app.
    convos = fetch_recent_conversations(max_conversations=5, headless=False)
    import json
    print(json.dumps(convos, indent=2, ensure_ascii=False))
