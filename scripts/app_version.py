"""
Shared helpers for reading/writing app_version.json — the config the
mobile app polls for update info and maintenance status.
"""

import json
import os
from datetime import datetime, timezone

DEFAULT_PATH = "data/app_version.json"

DEFAULT_APP_VERSION = {
    "latestVersionCode": 1,
    "latestVersionName": "1.0",
    "minSupportedVersionCode": 1,
    "forceUpdate": False,
    "updateUrl": "",
    "releaseNotes": {},
    "maintenance": {
        "isUnderMaintenance": False,
        "sync_by": "",
        "last_sync_at": "",
    },
    "metadata": {},
}


def load(path: str = DEFAULT_PATH) -> dict:
    if not os.path.exists(path):
        return json.loads(json.dumps(DEFAULT_APP_VERSION))  # deep copy
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(data: dict, path: str = DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_maintenance(
    is_under_maintenance: bool,
    sync_by: str,
    path: str = DEFAULT_PATH,
) -> dict:
    """
    Update only the maintenance block, preserving every other field
    (version info, release notes, metadata untouched). Used by automated
    scripts (e.g. the scraper) to flag maintenance status without
    needing to know about the rest of the config.
    """
    data = load(path)
    data.setdefault("maintenance", dict(DEFAULT_APP_VERSION["maintenance"]))
    data["maintenance"]["isUnderMaintenance"] = is_under_maintenance
    data["maintenance"]["sync_by"] = sync_by
    data["maintenance"]["last_sync_at"] = utc_now_iso()
    save(data, path)
    return data
