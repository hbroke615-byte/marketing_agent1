import time

from playwright.sync_api import TimeoutError, sync_playwright

from config import LINKEDIN_PROFILE_URL

LINKEDIN_STORAGE_STATE = "linkedin_state.json"


def send_dm(message, profile_url=None):
    """
    Automates sending a LinkedIn DM to a specific profile.
    If profile_url is provided, it navigates there; otherwise uses the default.
    """
    # Default target if none provided
    target_url = profile_url if profile_url else LINKEDIN_PROFILE_URL

    if not message or not message.strip():
        print("LinkedIn DM skipped: empty message.")
        return False

    with sync_playwright() as playwright:
        browser = None

        try:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(storage_state=LINKEDIN_STORAGE_STATE)
            page = context.new_page()

            print(f"Opening LinkedIn profile: {target_url}...")
            page.goto(target_url, timeout=60000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(5000)

            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(2000)

            try:
                # 1. Check if we are connected
                connect_button = page.locator("button[aria-label^='Invite'], a[href*='custom-invite']").first
                if connect_button.is_visible():
                    print("⚠️ Note: 'Connect' button is visible. You might not be connected to this profile.")

                # 2. Identify all potential Message buttons (usually <a> tags)
                message_selectors = [
                    "a[href*='messaging/compose']",
                    "button[aria-label^='Message']",
                    "a:has(svg#send-privately-medium)",
                    "a:has-text('Message')"
                ]
                
                locator = None
                for sel in message_selectors:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        locator = loc
                        break
                
                if not locator:
                    print("❌ No Message buttons found. This profile might be private or requires a Premium connection.")
                    return False

                message_box = None
                message_box_selectors = [
                    "div.msg-form__contenteditable",
                    "div.msg-composable-form__contenteditable",
                    "div[role='textbox'][aria-label^='Write a message']"
                ]

                # 3. Try clicking found buttons sequentially
                for i in range(min(locator.count(), 3)):
                    btn = locator.nth(i)
                    if not btn.is_visible(): continue
                    
                    print(f"鼠标 Attempting to click Message button #{i+1}...")
                    try:
                        btn.evaluate("el => el.scrollIntoView({block: 'center'})")
                        page.wait_for_timeout(1000)
                        
                        # Try direct click first
                        btn.click(force=True, timeout=5000)
                        
                        # Wait for box (increased to 15s)
                        for _ in range(15):
                            for mb_sel in message_box_selectors:
                                if page.locator(mb_sel).first.is_visible():
                                    message_box = page.locator(mb_sel).first
                                    break
                            if message_box: break
                            page.wait_for_timeout(1000)
                        
                        if not message_box:
                            # Try JS click if popup didn't appear
                            print(f"   (Button #{i+1} regular click didn't open box, trying JS click...)")
                            btn.evaluate("el => el.click()")
                            for _ in range(15):
                                for mb_sel in message_box_selectors:
                                    if page.locator(mb_sel).first.is_visible():
                                        message_box = page.locator(mb_sel).first
                                        break
                                if message_box: break
                                page.wait_for_timeout(1000)
                    except Exception as e:
                        print(f"   (Error with button #{i+1}: {e})")
                    
                    if message_box:
                        print(f"✅ Success! Message box appeared via button #{i+1}.")
                        break

                if not message_box:
                    print("⌛ Checking for full-page navigation/redirect...")
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                        for mb_sel in message_box_selectors:
                            if page.locator(mb_sel).first.is_visible():
                                message_box = page.locator(mb_sel).first
                                break
                    except: pass

                if not message_box:
                    print("❌ Could not trigger the message box. You may need to be connected to this person to message them.")
                    return False

                print("⌨️ Message box visible. Filling message...")
                message_box.click(force=True)
                message_box.fill(message.strip())
                page.wait_for_timeout(1000)
                
                # Robust 'wake up' for the Send button
                message_box.click() # Click again to focus
                message_box.press("End") 
                message_box.press("Enter") # New line then backspace often triggers it
                message_box.press("Backspace")
                page.wait_for_timeout(1500) 

                # Refined Send button selector based on user's HTML
                send_selectors = [
                    "button.msg-form__send-btn.artdeco-button--circle", # Specific circular button
                    "button.msg-form__send-btn",
                    "button:has(svg[data-test-icon^='send-privately'])",
                    "button[type='submit']:has-text('Send')",
                    "button:has-text('Send')"
                ]
                
                send_button = None
                print("🔍 Looking for Send button...")
                for sel in send_selectors:
                    loc = page.locator(sel).filter(has_not_text="Scheduling").first
                    if loc.is_visible():
                        send_button = loc
                        break
                
                if not send_button:
                    # Final fallback to any submit button in the footer
                    send_button = page.locator(".msg-form__footer button[type='submit'], .msg-form__footer button").first

                print("⌛ Waiting for Send button to enable...")
                try:
                    send_button.wait_for(state="visible", timeout=10000)
                    for i in range(10): # Increase polling to 10s
                        if send_button.is_enabled(): break
                        page.wait_for_timeout(1000)
                except: pass

                print("🚀 Clicking Send...")
                try:
                    # Use a real click with force=True to bypass any overlaps
                    send_button.click(force=True, timeout=5000)
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"⚠️ Regular click failed: {e}. Trying event click...")
                    send_button.dispatch_event("click")
                    page.wait_for_timeout(3000)

                # Final verification: message box should be cleared or gone
                # Sometimes the chat box stays open but clears the text.
                try:
                    if message_box.is_visible() and message_box.text_content().strip():
                        print("❌ Message box still has text. Attempting one last direct click...")
                        send_button.evaluate("el => el.click()")
                        page.wait_for_timeout(2000)
                except: pass

                try:
                    if message_box.is_visible() and message_box.text_content().strip():
                        print("❌ Final Attempt failed. Message text is still in the box.")
                        return False
                except: pass

                print("✨ LinkedIn message sent successfully.")
                time.sleep(2)
                return True
            except Exception as e:
                print(f"❌ Failed to send LinkedIn message: {e}")
                return False
        finally:
            if browser:
                browser.close()


if __name__ == "__main__":
    send_dm("Hello, this is a test message from the marketing campaign agent.")
