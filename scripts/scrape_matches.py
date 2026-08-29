"""
Scrape client-side-rendered match list content and write it as JSON to a file.

Local usage:
    pip install playwright beautifulsoup4
    playwright install --with-deps chromium
    python scrape_matches.py https://example.com/ output.json

In GitHub Actions, TARGET_URL and OUTPUT_PATH are passed via env vars
(see workflow).
"""

import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36"
)
TARGET_SELECTOR = "#match-list"
LOCAL_TZ = ZoneInfo("Asia/Yangon")

STATUS_MAP = {
    "ĐANG TRỰC TIẾP": "Live",
    "CHƯA BẮT ĐẦU": "Upcoming",
}

LEAGUE_MAP = {
    "AFC Giải vô địch Champions 2": "AFC Champions League Two",
    "Cúp Bóng đá Châu Á U20": "AFC U20 Asian Cup",
    "Giải bóng đá Serie A Italia": "Italy Serie A",
    "Giải bóng đá Ngoại hạng Anh": "England Premier League",
    "Giải bóng đá Hạng hai Đức": "Germany Bundesliga 2",
    "Giải Vô địch quốc gia Ả-rập Xê-út": "Saudi Pro League",
    "Giải bóng đá vô địch quốc gia Đức": "Germany Bundesliga",
    "Giải Bóng đá Vô địch Quốc gia Tây Ban Nha": "Spain LaLiga",
}

# Populated during parsing with any raw league name not found in LEAGUE_MAP,
# so we can report just the new ones that still need a translation.
unmapped_leagues: set[str] = set()


def get_target_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    url = os.environ.get("TARGET_URL")
    if not url:
        raise SystemExit("Provide a URL as an argument or set TARGET_URL env var.")
    return url


def get_output_path() -> str:
    if len(sys.argv) > 2:
        return sys.argv[2]
    return os.environ.get("OUTPUT_PATH", "vebo_events.json")


def fetch_html(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 412, "height": 915},
            timezone_id="Asia/Yangon",
        )
        page = context.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Don't just wait for "any child" of #match-list — it starts out
        # containing a loading spinner, which counts as a child and would
        # make wait_for_selector return immediately before real data loads.
        # Wait for the actual match card elements instead.
        try:
            page.wait_for_selector(f"{TARGET_SELECTOR} .match-card", timeout=20000)
        except Exception:
            print(f"[warn] No '.match-card' appeared under '{TARGET_SELECTOR}' "
                  f"after 20s, grabbing whatever is there now.", file=sys.stderr)
            page.wait_for_timeout(3000)

        element = page.query_selector(TARGET_SELECTOR)
        html = element.inner_html() if element else None

        browser.close()

        if html is None:
            raise SystemExit(f"Selector '{TARGET_SELECTOR}' not found on page.")
        return html


def parse_kickoff_timestamp(time_text: str | None, date_attr: str | None) -> str | None:
    """
    Combine the rendered time text (e.g. "18:00 - 29/08", already in
    Asia/Yangon local time because the browser context is pinned to that
    timezone) with the year from data-date (e.g. "2026-08-29") into a
    full ISO-8601 timestamp with offset, e.g. "2026-08-29T18:00:00+06:30".
    """
    if not time_text:
        return None

    match = re.match(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2})/(\d{1,2})", time_text)
    if not match:
        return None
    hour, minute, day, month = (int(g) for g in match.groups())

    year = datetime.now(LOCAL_TZ).year
    if date_attr:
        try:
            year = int(date_attr.split("-")[0])
        except (ValueError, IndexError):
            pass

    dt = datetime(year, month, day, hour, minute, tzinfo=LOCAL_TZ)
    return dt.isoformat()


def text_or_none(node) -> str | None:
    if node is None:
        return None
    text = node.get_text(strip=True)
    return text or None


def parse_team(node) -> dict | None:
    if node is None:
        return None
    logo = node.select_one("img.team__logo")
    name = node.select_one("span.team__name")
    return {
        "name": text_or_none(name),
        "logo": logo.get("src") if logo else None,
    }


def parse_match_card(card) -> dict:
    status_node = card.select_one(".match-card__status.streaming, .match-card__status")
    status_raw = text_or_none(status_node)
    status = STATUS_MAP.get(status_raw, status_raw)

    league_node = card.select_one(".match-card__league span")
    league_raw = text_or_none(league_node)
    if league_raw and league_raw not in LEAGUE_MAP:
        unmapped_leagues.add(league_raw)
    league = LEAGUE_MAP.get(league_raw, league_raw) if league_raw else None

    time_node = card.select_one(".match-time")
    score_node = card.select_one(".match-card__score")
    commentator_node = card.select_one(".match-card__stats-content a")
    link_node = card.select_one("a.link-match")

    teams = card.select(".match-card__teams .team")
    home_team = parse_team(teams[0]) if len(teams) > 0 else None
    away_team = parse_team(teams[1]) if len(teams) > 1 else None

    score = text_or_none(score_node)
    time_text = text_or_none(time_node)
    kickoff = parse_kickoff_timestamp(time_text, card.get("data-date"))

    return {
        "id": card.get("data-id"),
        "sport": card.get("data-sport"),
        "league": league,
        "status": status,
        "kickoff_time": kickoff,
        "home_team": home_team,
        "away_team": away_team,
        "score": score,
        "commentator": text_or_none(commentator_node),
        "url": link_node.get("href") if link_node else None,
    }


def parse_matches(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".match-card")
    return [parse_match_card(card) for card in cards]


if __name__ == "__main__":
    target_url = get_target_url()
    output_path = get_output_path()

    print(f"[info] Fetching: {target_url}", file=sys.stderr)
    raw_html = fetch_html(target_url)
    matches = parse_matches(raw_html)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"[info] Wrote {len(matches)} match(es) to {output_path}", file=sys.stderr)

    if unmapped_leagues:
        print(f"[info] Unmapped leagues found ({len(unmapped_leagues)}), "
              f"add these to LEAGUE_MAP:", file=sys.stderr)
        print(json.dumps(sorted(unmapped_leagues), ensure_ascii=False, indent=2),
              file=sys.stderr)
    else:
        print("[info] No unmapped leagues — all leagues resolved via LEAGUE_MAP.",
              file=sys.stderr)
