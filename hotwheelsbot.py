import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
PINCODE = os.environ.get("MY_PINCODE")

# Items to watch for. A product matches if its name contains any of these
# keywords. Use a broad term like "hot wheels" to be alerted on everything.
WISHLIST = [
    "porsche", "skyline", "nissan", "bugatti",
    "mazda", "toyota", "assorted",
]

# The search term sent to Blinkit.
SEARCH_QUERY = "hot wheels"
SEARCH_URL = f"https://blinkit.com/s/?q={SEARCH_QUERY.replace(' ', '%20')}"

# File used to remember what was already in stock (so we don't re-alert every
# run) and when we last sent a daily heartbeat. Persisted across GitHub Actions
# runs via actions/cache.
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))

# Send a "still alive" message once per day at/after this UTC hour.
# 4 AM UTC == 9:30 AM IST.
HEARTBEAT_HOUR = 4

MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_7_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 "
    "Mobile/15E148 Safari/604.1"
)


# --- TELEGRAM ---
def send_telegram(text):
    """Post a Markdown message to the configured chat. Returns True on success."""
    if not BOT_TOKEN or not CHAT_ID:
        print("Cannot send Telegram message: TELEGRAM_TOKEN/TELEGRAM_CHAT_ID not set.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code != 200:
            print(f"Telegram API error {resp.status_code}: {resp.text}")
            return False
        return True
    except requests.RequestException as e:
        print(f"Failed to reach Telegram: {e}")
        return False


# --- STATE ---
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Could not read state file ({e}); starting fresh.")
    return {"in_stock": [], "last_heartbeat_date": None, "last_error": None}


def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError as e:
        print(f"Could not write state file: {e}")


# --- SCRAPING ---
# Blinkit changes its markup periodically; try several known selectors.
PRODUCT_CARD_SELECTORS = [
    "[data-testid='product-card']",
    "[data-pf='reset']",
    "div[role='button'][id]",
    ".Product__UpdatedPlpProductContainer-sc-11dk8zk-0",
]

LOCATION_INPUT_SELECTORS = [
    "[placeholder*='delivery location']",
    "[name='select-locality']",
    "input[type='text']",
]


def set_location(page):
    """Enter the pincode and pick the first suggestion."""
    page.goto("https://blinkit.com/", wait_until="domcontentloaded", timeout=60000)

    located = False
    for selector in LOCATION_INPUT_SELECTORS:
        try:
            page.wait_for_selector(selector, timeout=8000)
            page.click(selector)
            page.fill(selector, PINCODE)
            located = True
            break
        except PlaywrightTimeout:
            continue

    if not located:
        raise RuntimeError("Could not find Blinkit's location input box.")

    # Pick the first suggestion in the dropdown.
    for selector in [
        ".LocationSearchList__LocationDetailContainer-sc-93rfr7-0",
        ".LocationSearchList__LocationDetailContainer",
        "[class*='LocationDetailContainer']",
        "[class*='LocationSearchList']",
    ]:
        try:
            page.wait_for_selector(selector, timeout=8000)
            page.click(selector)
            page.wait_for_timeout(4000)
            return
        except PlaywrightTimeout:
            continue

    raise RuntimeError("Pincode entered but no location suggestion appeared.")


def find_product_cards(page):
    for selector in PRODUCT_CARD_SELECTORS:
        cards = page.query_selector_all(selector)
        if cards:
            return cards, selector
    return [], None


def scan_blinkit():
    """Return (matches, total_scanned). Raises on hard scraping failures."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=MOBILE_USER_AGENT)
        page = context.new_page()
        try:
            print(f"Setting location to {PINCODE}...")
            set_location(page)

            print(f"Searching Blinkit for '{SEARCH_QUERY}'...")
            page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            cards, used_selector = find_product_cards(page)
            print(f"Found {len(cards)} product cards (selector: {used_selector}).")

            matches = []
            for card in cards:
                text = card.inner_text().lower()
                # "add" button present => in stock (vs "out of stock"/"notify me").
                in_stock = "add" in text and "out of stock" not in text
                if in_stock and any(car in text for car in WISHLIST):
                    name = text.split("\n")[0].strip().upper()
                    if name and name not in matches:
                        matches.append(name)

            return matches, len(cards)
        finally:
            browser.close()


# --- MAIN ---
def validate_config():
    missing = [
        name
        for name, value in [
            ("TELEGRAM_TOKEN", BOT_TOKEN),
            ("TELEGRAM_CHAT_ID", CHAT_ID),
            ("MY_PINCODE", PINCODE),
        ]
        if not value
    ]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def run_tracker():
    validate_config()
    state = load_state()
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    try:
        matches, total = scan_blinkit()
    except Exception as e:
        # The scraper broke (selectors changed, site down, timeout). Tell the
        # user once instead of failing silently, then re-raise so the run is
        # marked failed in GitHub Actions.
        error_text = f"{type(e).__name__}: {e}"
        print(f"Scraping failed: {error_text}")
        if state.get("last_error") != error_text:
            send_telegram(
                "⚠️ *HOT WHEELS BOT ERROR*\n\n"
                "The tracker couldn't read Blinkit. Selectors may have changed "
                "or the site is unreachable.\n\n"
                f"`{error_text}`"
            )
            state["last_error"] = error_text
            save_state(state)
        raise

    # Scrape succeeded; clear any sticky error.
    state["last_error"] = None

    previous = set(state.get("in_stock", []))
    current = set(matches)
    newly_in_stock = sorted(current - previous)

    if newly_in_stock:
        msg = "🏎️ *HOT WHEELS DROPPED!*\n\n"
        msg += "\n".join(f"• {m}" for m in newly_in_stock)
        msg += f"\n\n📍 Pincode: {PINCODE}\n🔗 [Shop Blinkit]({SEARCH_URL})"
        send_telegram(msg)
        print(f"Alerted on {len(newly_in_stock)} new item(s).")
    else:
        print(f"No new matches. {len(current)} match(es) already known, {total} scanned.")

    # Daily heartbeat: confirm the bot is alive even when nothing's in stock.
    if now.hour >= HEARTBEAT_HOUR and state.get("last_heartbeat_date") != today:
        send_telegram(
            "☀️ *DAILY HEARTBEAT*\n\n"
            "✅ *Status:* Active & Hunting\n"
            f"📍 *Location:* {PINCODE}\n"
            f"📦 *Items Scanned:* {total} on page\n"
            f"🎯 *In Stock Now:* {len(current)} wishlist match(es)"
        )
        state["last_heartbeat_date"] = today
        print("Heartbeat sent.")

    state["in_stock"] = sorted(current)
    save_state(state)


if __name__ == "__main__":
    run_tracker()
