"""
Scrape the SET (Stock Exchange of Thailand) market overview index snapshot.

Usage:
    pip install playwright
    playwright install --with-deps chromium
    python scrape_set_index.py

Prints, in this order: status, update_at, set, value
"""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

TARGET_URL = "https://www.set.or.th/en/market/index/set/overview"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36"
)
LOCAL_TZ = ZoneInfo("Asia/Yangon")

# Kept loose (container class + generic "span") rather than pinned to
# state-specific classes like ".market-close", since market status class
# names likely change between open/closed/intermission states.
SET_VALUE_SELECTOR = ".quote-info-left-values .stock-info"
MARKET_STATUS_SELECTOR = ".quote-market-status span"
LAST_UPDATE_SELECTOR = ".quote-market-lastInfo span"
VALUE_MBAHT_SELECTOR = ".quote-market-cost span"


def now_str() -> str:
    return datetime.now(LOCAL_TZ).isoformat()


def text_or_none(page, selector: str) -> str | None:
    el = page.query_selector(selector)
    if el is None:
        return None
    text = el.inner_text().strip()
    return text or None


def wait_for_digits(page, selector: str, timeout_ms: int = 20000) -> None:
    """Wait until the element exists and its text contains a digit —
    i.e. real data has rendered, not an empty/placeholder state."""
    page.wait_for_function(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            return /\\d/.test(el.innerText || "");
        }""",
        arg=selector,
        timeout=timeout_ms,
    )


def scrape() -> dict:
    start_time = datetime.now(LOCAL_TZ)
    print(f"[info] Script started at: {start_time.isoformat()}", file=sys.stderr)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            timezone_id="Asia/Yangon",
        )
        page = context.new_page()

        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)

        loaded_time = datetime.now(LOCAL_TZ)
        print(f"[info] Document loaded at: {loaded_time.isoformat()}", file=sys.stderr)
        duration = (loaded_time - start_time).total_seconds()
        print(f"[info] Duration (start -> document loaded): {duration:.2f}s",
              file=sys.stderr)

        # CSR page — the index value renders after JS/API calls finish, so
        # wait for actual digits rather than just the element existing.
        try:
            wait_for_digits(page, SET_VALUE_SELECTOR, timeout_ms=20000)
        except Exception:
            print(f"[warn] '{SET_VALUE_SELECTOR}' had no digits after 20s, "
                  f"grabbing whatever is there now.", file=sys.stderr)
            page.wait_for_timeout(3000)

        result = {
            "status": text_or_none(page, MARKET_STATUS_SELECTOR),
            "update_at": text_or_none(page, LAST_UPDATE_SELECTOR),
            "set": text_or_none(page, SET_VALUE_SELECTOR),
            "value": text_or_none(page, VALUE_MBAHT_SELECTOR),
        }

        browser.close()
        return result


if __name__ == "__main__":
    data = scrape()
    print(data["status"])
    print(data["update_at"])
    print(data["set"])
    print(data["value"])
