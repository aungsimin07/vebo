"""
Scrape the SET (Stock Exchange of Thailand) market overview index at a
precise target time, for one of four daily slots, and write the result
into today.json.

The script is intentionally decoupled from *when* it's triggered — the
caller (workflow) tells it which slot this run is for via SLOT (env var
or first CLI arg), and the script sleeps until that slot's exact target
clock time (Asia/Yangon) before reading the final values. This means the
trigger can fire up to a few minutes early (e.g. to account for
cron-job.org's own delay + Playwright browser startup time) without
affecting what time the values are actually captured at.

Usage:
    pip install playwright
    playwright install --with-deps chromium
    SLOT=pre_morning python scrape_set_index.py
    # or: python scrape_set_index.py pre_morning
"""

import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

TARGET_URL = "https://www.set.or.th/en/market/index/set/overview"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36"
)
LOCAL_TZ = ZoneInfo("Asia/Yangon")

SET_VALUE_SELECTOR = ".quote-info-left-values .stock-info"
MARKET_STATUS_SELECTOR = ".quote-market-status span"
LAST_UPDATE_SELECTOR = ".quote-market-lastInfo span"
VALUE_MBAHT_SELECTOR = ".quote-market-cost span"

# slot -> exact target time (Asia/Yangon) that slot's values should be
# captured at. Triggers should fire shortly before this, not exactly at it.
SLOT_TARGET_TIME = {
    "pre_morning": (9, 30),
    "morning": (12, 1),
    "pre_evening": (14, 0),
    "evening": (16, 30),
}
SLOT_ORDER = ["pre_morning", "morning", "pre_evening", "evening"]

# If a trigger fires more than this many seconds before its target time,
# something's likely misconfigured (e.g. wrong cron time) — fail loudly
# rather than let the job hang silently for a long time.
MAX_WAIT_SECONDS = 180


def get_slot() -> str:
    slot = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SLOT", "")
    slot = slot.strip()
    if slot not in SLOT_TARGET_TIME:
        raise SystemExit(
            f"Invalid or missing slot: {slot!r}. "
            f"Expected one of: {', '.join(SLOT_ORDER)}"
        )
    return slot


def get_output_path() -> str:
    return os.environ.get("TODAY_JSON_PATH", "today.json")


def empty_slot_values() -> dict:
    return {"status": None, "update_at": None, "set": None, "value": None, "twod": None}


def default_today() -> dict:
    return {slot: empty_slot_values() for slot in SLOT_ORDER}


def load_today(path: str) -> dict:
    if not os.path.exists(path):
        return default_today()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Guard against a malformed/partial file missing a slot key.
    for slot in SLOT_ORDER:
        data.setdefault(slot, empty_slot_values())
    return data


def save_today(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def last_digit(s: str | None) -> str | None:
    if not s:
        return None
    for ch in reversed(s):
        if ch.isdigit():
            return ch
    return None


def digit_before_dot(s: str | None) -> str | None:
    if not s:
        return None
    idx = s.find(".")
    if idx <= 0:
        return None
    ch = s[idx - 1]
    return ch if ch.isdigit() else None


def compute_twod(set_str: str | None, value_str: str | None) -> str | None:
    d1 = last_digit(set_str)
    d2 = digit_before_dot(value_str)
    if d1 is None or d2 is None:
        return None
    return f"{d1}{d2}"


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


def sleep_until(target_dt: datetime) -> None:
    now = datetime.now(LOCAL_TZ)
    wait_seconds = (target_dt - now).total_seconds()

    if wait_seconds <= 0:
        print(f"[warn] Already at/past target {target_dt.isoformat()} "
              f"(behind by {-wait_seconds:.1f}s) — proceeding immediately.",
              file=sys.stderr)
        return

    if wait_seconds > MAX_WAIT_SECONDS:
        raise SystemExit(
            f"Target time {target_dt.isoformat()} is {wait_seconds:.1f}s away, "
            f"exceeding MAX_WAIT_SECONDS={MAX_WAIT_SECONDS}. Refusing to sleep "
            f"that long — check the trigger schedule."
        )

    print(f"[info] Sleeping {wait_seconds:.1f}s until target time "
          f"{target_dt.isoformat()}", file=sys.stderr)
    time.sleep(wait_seconds)
    print(f"[info] Woke up at: {datetime.now(LOCAL_TZ).isoformat()}", file=sys.stderr)


def scrape(target_dt: datetime) -> dict:
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

        try:
            wait_for_digits(page, SET_VALUE_SELECTOR, timeout_ms=20000)
        except Exception:
            print(f"[warn] '{SET_VALUE_SELECTOR}' had no digits after 20s, "
                  f"grabbing whatever is there now.", file=sys.stderr)
            page.wait_for_timeout(3000)

        # Wait until the exact target time before reading final values —
        # the page stays open and the ticker keeps updating live, so
        # re-querying now (rather than right after initial load) captures
        # the value as of the target time, not up to a minute early.
        sleep_until(target_dt)

        status = text_or_none(page, MARKET_STATUS_SELECTOR)
        update_at = text_or_none(page, LAST_UPDATE_SELECTOR)
        set_value = text_or_none(page, SET_VALUE_SELECTOR)
        value_mbaht = text_or_none(page, VALUE_MBAHT_SELECTOR)

        browser.close()

        return {
            "status": status,
            "update_at": update_at,
            "set": set_value,
            "value": value_mbaht,
            "twod": compute_twod(set_value, value_mbaht),
        }


if __name__ == "__main__":
    slot = get_slot()
    output_path = get_output_path()

    hour, minute = SLOT_TARGET_TIME[slot]
    now = datetime.now(LOCAL_TZ)
    target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    print(f"[info] Slot: {slot} -> target time: {target_dt.isoformat()}",
          file=sys.stderr)

    result = scrape(target_dt)

    print(result["status"])
    print(result["update_at"])
    print(result["set"])
    print(result["value"])
    print(result["twod"])

    if slot == "pre_morning":
        # First run of the day — reset every slot (clearing yesterday's
        # leftover values) then fill in this run's own slot.
        data = default_today()
        print("[info] pre_morning run — cleared all slots for the new day.",
              file=sys.stderr)
    else:
        data = load_today(output_path)

    data[slot] = result
    save_today(data, output_path)
    print(f"[info] Wrote slot '{slot}' to {output_path}", file=sys.stderr)
