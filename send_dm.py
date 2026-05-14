import time

from playwright.sync_api import TimeoutError, sync_playwright

from config import LINKEDIN_PROFILE_URL

LINKEDIN_STORAGE_STATE = "linkedin_state.json"


def send_dm(message, profile_url=LINKEDIN_PROFILE_URL):
    """Send a LinkedIn DM using a saved Playwright login session."""
    if not message or not message.strip():
        print("LinkedIn DM skipped: empty message.")
        return False

    with sync_playwright() as playwright:
        browser = None

        try:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(storage_state=LINKEDIN_STORAGE_STATE)
            page = context.new_page()

            print("Opening LinkedIn profile...")
            page.goto(profile_url, timeout=60000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(5000)

            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(2000)

            try:
                message_buttons = page.locator("button[aria-label^='Message']")
                count = message_buttons.count()
                print(f"Found Message buttons: {count}")

                if count == 0:
                    print("No Message button available.")
                    return False

                visible_button = None
                for index in range(count):
                    button = message_buttons.nth(index)
                    if button.is_visible():
                        visible_button = button
                        break

                if not visible_button:
                    print("Message button exists but is hidden.")
                    return False

                visible_button.scroll_into_view_if_needed()
                visible_button.click()
            except TimeoutError:
                print("Timeout while locating Message button.")
                return False

            message_box = page.locator("div.msg-form__contenteditable").first
            message_box.wait_for(state="visible", timeout=15000)
            message_box.click()
            message_box.fill(message.strip())

            send_button = page.locator("button", has_text="Send").first
            send_button.click()

            print("LinkedIn message sent successfully.")
            time.sleep(3)
            return True
        except Exception as e:
            print(f"Failed to send LinkedIn message: {e}")
            return False
        finally:
            if browser:
                browser.close()


if __name__ == "__main__":
    send_dm("Hello, this is a test message from the marketing campaign agent.")
