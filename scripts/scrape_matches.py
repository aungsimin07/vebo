"""
Simple test: scrape client-side-rendered content and print it.

Local usage:
    pip install playwright
    playwright install --with-deps chromium
    python scrape_matches.py https://example.com/

In GitHub Actions, TARGET_URL is passed via env var (see workflow).
"""

import os
import sys
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36"
)
TARGET_SELECTOR = "#match-list"


def get_target_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    url = os.environ.get("TARGET_URL")
    if not url:
        raise SystemExit("Provide a URL as an argument or set TARGET_URL env var.")
    return url


def scrape(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 412, "height": 915},
        )
        page = context.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        try:
            page.wait_for_selector(f"{TARGET_SELECTOR} *", timeout=15000)
        except Exception:
            print(f"[warn] '{TARGET_SELECTOR}' had no children after 15s, "
                  f"grabbing whatever is there now.", file=sys.stderr)
            page.wait_for_timeout(3000)

        element = page.query_selector(TARGET_SELECTOR)
        html = element.inner_html() if element else None

        browser.close()

        if html is None:
            raise SystemExit(f"Selector '{TARGET_SELECTOR}' not found on page.")
        return html


if __name__ == "__main__":
    target_url = get_target_url()
    print(f"[info] Fetching: {target_url}", file=sys.stderr)
    content = scrape(target_url)
    print(content)
