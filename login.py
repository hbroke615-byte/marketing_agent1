from playwright.sync_api import sync_playwright

def save_session():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()

        page = context.new_page()
        page.goto("https://www.linkedin.com/login")

        print("\n👉 LOGIN MANUALLY in the browser window")
        print("👉 After login is successful, come back here and press ENTER...\n")

        input()

        # Save session
        context.storage_state(path="linkedin_state.json")

        print("✅ Session saved successfully!")

        browser.close()

if __name__ == "__main__":
    save_session()