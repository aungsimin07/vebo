"""
CLI to manage app_version.json — update version info, force-update flag,
release notes, metadata, and maintenance status.

Usage examples:
    python manage_app_version.py --path data/app_version.json \
        --latest-version-code 2 --latest-version-name 1.1 \
        --release-notes-json '{"en":"Bug fixes.","my":"..."}'

    python manage_app_version.py --force-update true --update-url "https://play.google.com/..."

    python manage_app_version.py --maintenance true --sync-by "manual"
"""

import argparse
import json
import sys

import app_version


def parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"Expected true/false, got: {value}")


def parse_json_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {e}")
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("Expected a JSON object")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manage app_version.json")
    p.add_argument("--path", default=app_version.DEFAULT_PATH)
    p.add_argument("--latest-version-code", type=int)
    p.add_argument("--latest-version-name", type=str)
    p.add_argument("--min-supported-version-code", type=int)
    p.add_argument("--force-update", type=parse_bool)
    p.add_argument("--update-url", type=str)
    p.add_argument("--release-notes-json", type=parse_json_object,
                    help='JSON object merged into releaseNotes, e.g. \'{"en":"...","my":"..."}\'')
    p.add_argument("--metadata-json", type=parse_json_object,
                    help='JSON object merged into metadata, e.g. \'{"key":"value"}\'')
    p.add_argument("--maintenance", type=parse_bool)
    p.add_argument("--sync-by", type=str, default="manage_app_version.py (manual)")
    return p


def main() -> None:
    args = build_parser().parse_args()
    data = app_version.load(args.path)

    if args.latest_version_code is not None:
        data["latestVersionCode"] = args.latest_version_code
    if args.latest_version_name is not None:
        data["latestVersionName"] = args.latest_version_name
    if args.min_supported_version_code is not None:
        data["minSupportedVersionCode"] = args.min_supported_version_code
    if args.force_update is not None:
        data["forceUpdate"] = args.force_update
    if args.update_url is not None:
        data["updateUrl"] = args.update_url

    if args.release_notes_json:
        data.setdefault("releaseNotes", {})
        data["releaseNotes"].update(args.release_notes_json)

    if args.metadata_json:
        data.setdefault("metadata", {})
        data["metadata"].update(args.metadata_json)

    if args.maintenance is not None:
        data.setdefault("maintenance", dict(app_version.DEFAULT_APP_VERSION["maintenance"]))
        data["maintenance"]["isUnderMaintenance"] = args.maintenance
        data["maintenance"]["sync_by"] = args.sync_by
        data["maintenance"]["last_sync_at"] = app_version.utc_now_iso()

    app_version.save(data, args.path)
    print(f"[info] Updated {args.path}", file=sys.stderr)
    print(json.dumps(data, ensure_ascii=False, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()