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

import app_version

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36"
)
TARGET_SELECTOR = "#match-list"
LOCAL_TZ = ZoneInfo("Asia/Yangon")
SCRIPT_NAME = "scrape_matches.py"


class ScrapeError(Exception):
    """Raised when the scrape ran without a hard error but didn't find
    what we expect — e.g. the site's structure changed, or nothing
    rendered at all. Distinct from network/timeout exceptions, but
    handled the same way (flags maintenance, leaves prior data alone)."""

STATUS_MAP = {
    "ĐANG TRỰC TIẾP": "1H",
    "CHƯA BẮT ĐẦU": "NS",
}

# Populated during parsing with any raw status text not found in
# STATUS_MAP, so we can report new statuses that need a code.
unmapped_statuses: set[str] = set()

LEAGUE_MAP_ENV_VAR = "LEAGUE_MAP_JSON"


def load_league_map() -> dict:
    raw = os.environ.get(LEAGUE_MAP_ENV_VAR, "")
    if not raw.strip():
        print(f"[warn] {LEAGUE_MAP_ENV_VAR} is empty/unset — league names "
              f"will be left untranslated.", file=sys.stderr)
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[warn] Failed to parse {LEAGUE_MAP_ENV_VAR} as a JSON object "
              f"({e}) — league names will be left untranslated.", file=sys.stderr)
        return {}


LEAGUE_MAP = load_league_map()

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


def get_app_version_path() -> str:
    return os.environ.get("APP_VERSION_PATH", app_version.DEFAULT_PATH)


def fetch_html(url: str) -> tuple[str, bool]:
    """Returns (inner_html_of_match_list, selector_wait_succeeded)."""
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
        selector_ok = True
        try:
            page.wait_for_selector(f"{TARGET_SELECTOR} .match-card", timeout=20000)
        except Exception:
            selector_ok = False
            print(f"[warn] No '.match-card' appeared under '{TARGET_SELECTOR}' "
                  f"after 20s, grabbing whatever is there now.", file=sys.stderr)
            page.wait_for_timeout(3000)

        element = page.query_selector(TARGET_SELECTOR)
        html = element.inner_html() if element else None

        browser.close()

        if html is None:
            raise ScrapeError(f"Selector '{TARGET_SELECTOR}' not found on page.")
        return html, selector_ok


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


def parse_score(score_text: str | None) -> tuple[int, int]:
    """Extract 'X - Y' into (X, Y) as ints. Falls back to (0, 0) when
    there's no score yet (e.g. an upcoming match)."""
    if not score_text:
        return 0, 0
    match = re.match(r"(\d+)\s*-\s*(\d+)", score_text)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


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
    if status_raw and status_raw not in STATUS_MAP:
        unmapped_statuses.add(status_raw)
    status_code = STATUS_MAP.get(status_raw, status_raw)

    league_node = card.select_one(".match-card__league span")
    league_raw = text_or_none(league_node)
    if league_raw and league_raw not in LEAGUE_MAP:
        unmapped_leagues.add(league_raw)
    league = LEAGUE_MAP.get(league_raw, league_raw) if league_raw else None

    time_node = card.select_one(".match-time")
    score_node = card.select_one(".match-card__score")
    link_node = card.select_one("a.link-match")

    teams = card.select(".match-card__teams .team")
    home_team = parse_team(teams[0]) if len(teams) > 0 else None
    away_team = parse_team(teams[1]) if len(teams) > 1 else None

    time_text = text_or_none(time_node)
    kickoff = parse_kickoff_timestamp(time_text, card.get("data-date"))
    home_score, away_score = parse_score(text_or_none(score_node))

    return {
        "league": league,
        "kickoff_time": kickoff,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "status_code": status_code,
        "url": link_node.get("href") if link_node else None,
    }


def parse_matches(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    # Only football matches — other sports (and their leagues) are ignored
    # entirely, so league mapping/unmapped-league tracking never sees them.
    cards = soup.select('.match-card[data-sport="football"]')
    return [parse_match_card(card) for card in cards]


def detect_common_base_url(urls: list[str]) -> str | None:
    """
    Find the shared directory prefix across a set of badge URLs, e.g.:
        https://img.gvapi.cc/football/team/1cb3d08...png!w80
        https://img.gvapi.cc/football/team/62572999...png!w80
    -> "https://img.gvapi.cc/football/team/"

    Computed fresh each run (rather than hardcoded) since the CDN path
    could change over time. Needs at least 2 distinct URLs to be safe —
    with only one sample, the "common prefix" would be the whole URL.
    """
    unique_urls = sorted({u for u in urls if u})
    if len(unique_urls) < 2:
        return None
    prefix = os.path.commonprefix(unique_urls)
    last_slash = prefix.rfind("/")
    if last_slash == -1:
        return None
    base = prefix[: last_slash + 1]
    if not base.startswith(("http://", "https://")):
        return None
    return base


def strip_badge_base(url: str | None, base_url: str | None) -> str | None:
    if not url:
        return url
    if base_url and url.startswith(base_url):
        return url[len(base_url):]
    return url


def build_output(matches: list[dict]) -> dict:
    """Group flat match dicts by league into the target structure."""
    all_logo_urls = [
        m["home_team"]["logo"] if m["home_team"] else None for m in matches
    ] + [
        m["away_team"]["logo"] if m["away_team"] else None for m in matches
    ]
    base_url = detect_common_base_url(all_logo_urls)
    if base_url:
        print(f"[info] Team badge base URL detected: {base_url} "
              f"(stripped from strHomeTeamBadge/strAwayTeamBadge)", file=sys.stderr)
    else:
        print("[warn] Could not determine a common team badge base URL "
              "(fewer than 2 distinct badge URLs this run) — storing full URLs.",
              file=sys.stderr)

    leagues_map: dict[str, list[dict]] = {}
    for m in matches:
        league_name = m["league"] or "Unknown"
        home_logo = m["home_team"]["logo"] if m["home_team"] else None
        away_logo = m["away_team"]["logo"] if m["away_team"] else None
        event = {
            "strTimestamp": m["kickoff_time"],
            "strHomeTeam": m["home_team"]["name"] if m["home_team"] else None,
            "strHomeTeamBadge": strip_badge_base(home_logo, base_url),
            "intHomeScore": m["home_score"],
            "strAwayTeam": m["away_team"]["name"] if m["away_team"] else None,
            "strAwayTeamBadge": strip_badge_base(away_logo, base_url),
            "intAwayScore": m["away_score"],
            "strStatus": m["status_code"],
            "url": m["url"],
        }
        leagues_map.setdefault(league_name, []).append(event)

    leagues = []
    for league_name in sorted(leagues_map.keys()):
        events = leagues_map[league_name]
        events.sort(key=lambda e: (e["strTimestamp"] is None, e["strTimestamp"]))
        leagues.append({"strLeague": league_name, "events": events})

    return {"leagues": leagues}


def run(target_url: str, output_path: str, app_version_path: str) -> None:
    print(f"[info] Fetching: {target_url}", file=sys.stderr)
    raw_html, selector_ok = fetch_html(target_url)

    # "Failed" means: didn't get what we wanted — not just an HTTP/timeout
    # error, but also the site loading fine while the match-card elements
    # we depend on never showed up (structure changed, JS broke, etc).
    total_cards = len(BeautifulSoup(raw_html, "html.parser").select(".match-card"))
    if not selector_ok or total_cards == 0:
        raise ScrapeError(
            f"No '.match-card' elements found on the page "
            f"(selector_wait_ok={selector_ok}, total_cards={total_cards})."
        )

    matches = parse_matches(raw_html)
    output = build_output(matches)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write("\n")

    total_events = sum(len(l["events"]) for l in output["leagues"])
    print(f"[info] Wrote {total_events} event(s) across {len(output['leagues'])} "
          f"league(s) to {output_path}", file=sys.stderr)

    if unmapped_leagues:
        print(f"[info] Unmapped leagues found ({len(unmapped_leagues)}), "
              f"add these to LEAGUE_MAP_JSON:", file=sys.stderr)
        print(json.dumps(sorted(unmapped_leagues), ensure_ascii=False, indent=2),
              file=sys.stderr)
    else:
        print("[info] No unmapped leagues — all leagues resolved via LEAGUE_MAP_JSON.",
              file=sys.stderr)

    if unmapped_statuses:
        print(f"[info] Unmapped statuses found ({len(unmapped_statuses)}), "
              f"add these to STATUS_MAP:", file=sys.stderr)
        print(json.dumps(sorted(unmapped_statuses), ensure_ascii=False, indent=2),
              file=sys.stderr)

    app_version.set_maintenance(False, SCRIPT_NAME, path=app_version_path)
    print(f"[info] Marked maintenance=false in {app_version_path}", file=sys.stderr)


if __name__ == "__main__":
    target_url = get_target_url()
    output_path = get_output_path()
    app_version_path = get_app_version_path()

    try:
        run(target_url, output_path, app_version_path)
    except Exception as e:
        print(f"[error] Scrape failed: {e}", file=sys.stderr)
        app_version.set_maintenance(True, SCRIPT_NAME, path=app_version_path)
        print(f"[info] Marked maintenance=true in {app_version_path}", file=sys.stderr)
        # Deliberately don't touch output_path here — leave the last known
        # good vebo_events.json in place rather than overwrite it with
        # empty/partial data.
        sys.exit(1)
