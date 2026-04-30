import os
import time
import requests
from playwright.sync_api import sync_playwright

# Load secrets from GitHub Environment
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
PINCODE = os.environ.get("MY_PINCODE")

# Your Search List
WISHLIST = ['porsche', 'skyline', 'nissan', 'bugatti', 'mazda', 'toyota', 'assorted']

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def run_tracker():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Using a mobile-style user agent helps Blinkit load faster
        context = browser.new_context(user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 14_7_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1")
        page = context.new_page()

        try:
            print(f"Setting location to {PINCODE}...")
            page.goto("https://blinkit.com/", wait_until="networkidle")
            
            # Location Setting Logic
            page.wait_for_selector("[placeholder*='delivery location']", timeout=10000)
            page.click("[placeholder*='delivery location']")
            page.fill("[placeholder*='delivery location']", PINCODE)
            
            page.wait_for_selector(".LocationSearchList__LocationDetailContainer", timeout=10000)
            page.click(".LocationSearchList__LocationDetailContainer")
            page.wait_for_timeout(5000) # Wait for page to refresh location

            print("Searching for Hot Wheels...")
            page.goto(f"https://blinkit.com/s/?q=hot%20wheels", wait_until="networkidle")
            page.wait_for_timeout(5000)

            products = page.query_selector_all("[data-testid='product-card']")
            matches = []

            for product in products:
                text = product.inner_text().lower()
                if "add" in text and any(car in text for car in WISHLIST):
                    name = text.split('\n')[0].upper()
                    matches.append(name)

            if matches:
                msg = "🏎️ *HOT WHEELS FOUND!*\n\n" + "\n".join([f"• {m}" for m in matches])
                msg += f"\n\n📍 Pincode: {PINCODE}\n🔗 [Shop Blinkit](https://blinkit.com/s/?q=hot%20wheels)"
                send_telegram(msg)
                print(f"Success: Found {len(matches)} items.")
            else:
                print("No matches found today.")

        except Exception as e:
            print(f"Error: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run_tracker()
