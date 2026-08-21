#!/usr/bin/python
"""Back up a MySSI account as completely as the API allows.

Writes the untouched JSON for every command that returns data, a CSV for
every table inside those payloads, and any media files referenced by them.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests

from divessi.api import SSIApi, curate_divelog
from divessi.constants import (
    DEFAULT_DELAY,
    DISCOVERY_CANDIDATES,
    DISCOVERY_COOLDOWN,
    KNOWN_COMMANDS,
    MEDIA_ALLOWED_HOST_SUFFIX,
    MEDIA_EXTENSIONS,
)
from divessi.discover import discover
from divessi.exceptions import AuthenticationException, DivessiException
from divessi.flatten import tables, write_csv

logger = logging.getLogger("divessi.export")

URL_PATTERN = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def credentials(path: str = None) -> tuple:
    """Return (username, password) from the first source that has them.

    Order: an explicit credentials file, then SSI_USERNAME / SSI_PASSWORD in
    the environment, then params.py. A credentials file holds the email on
    the first line and the password on the second; blank lines and lines
    starting with # are ignored.
    """
    if path:
        try:
            lines = [
                line.strip()
                for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        except OSError as exc:
            sys.exit(f"Cannot read credentials file {path}: {exc}")

        if len(lines) < 2:
            sys.exit(f"Credentials file {path} needs two lines: email, then password.")

        return lines[0], lines[1]

    username = os.environ.get("SSI_USERNAME")
    password = os.environ.get("SSI_PASSWORD")

    if username and password:
        return username, password

    try:
        from params import password as file_password
        from params import username as file_username
    except ImportError:
        sys.exit(
            "No credentials. Either set SSI_USERNAME and SSI_PASSWORD, or copy "
            "params.example.py to params.py and fill it in."
        )

    return file_username, file_password


def safe_name(value: str) -> str:
    return SAFE_NAME.sub("_", value).strip("_") or "unnamed"


def harvest_urls(payload: Any) -> List[str]:
    """Collect media URLs appearing anywhere in a payload."""
    found = []
    blob = json.dumps(payload, ensure_ascii=False)

    for url in URL_PATTERN.findall(blob):
        url = url.rstrip(".,);")
        path = urlparse(url).path.lower()
        if path.endswith(MEDIA_EXTENSIONS):
            found.append(url)

    return sorted(set(found))


def download_media(
    urls: List[str],
    directory: Path,
    session: requests.Session,
    any_host: bool = False,
    delay: float = DEFAULT_DELAY,
) -> Dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    saved, skipped, failed = 0, 0, 0

    for url in urls:
        host = (urlparse(url).hostname or "").lower()
        if not any_host and not host.endswith(MEDIA_ALLOWED_HOST_SUFFIX):
            logger.info("skipping media on foreign host %s", host)
            skipped += 1
            continue

        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
        name = safe_name(Path(urlparse(url).path).name)
        target = directory / f"{digest}_{name}"

        if target.exists():
            skipped += 1
            continue

        try:
            response = session.get(url, timeout=60)
            response.raise_for_status()
            target.write_bytes(response.content)
            saved += 1
        except requests.RequestException as exc:
            logger.warning("media download failed for %s: %s", url, exc)
            failed += 1

        if delay:
            time.sleep(delay)

    return {"found": len(urls), "saved": saved, "skipped": skipped, "failed": failed}


def dump_payload(what: str, payload: Any, out: Path) -> Dict[str, Any]:
    raw_dir = out / "raw"
    csv_dir = out / "csv"
    raw_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{safe_name(what)}.json"
    raw_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    written = {}
    for table_name, rows in tables(payload):
        if not rows:
            continue
        csv_path = csv_dir / f"{safe_name(what)}__{safe_name(table_name)}.csv"
        written[csv_path.name] = write_csv(rows, csv_path)

    return {
        "raw": raw_path.name,
        "raw_bytes": raw_path.stat().st_size,
        "tables": written,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="export", help="output directory (default: export)"
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="probe all candidate commands instead of only the known ones",
    )
    parser.add_argument("--only", help="comma separated list of commands to fetch")
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="seconds to wait between requests (default: %(default)s)",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=DISCOVERY_COOLDOWN,
        help=(
            "seconds to wait once when the server stops answering, before "
            "retrying (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--no-media", action="store_true", help="do not download referenced media"
    )
    parser.add_argument(
        "--media-any-host",
        action="store_true",
        help="download media from hosts outside divessi.com as well",
    )
    parser.add_argument(
        "--credentials",
        metavar="FILE",
        help="file with the email on line 1 and the password on line 2",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )
    # Progress must show up live even when stdout is a pipe.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    username, password = credentials(args.credentials)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    print(f"authenticating as {username} ...")

    try:
        api = SSIApi(username, password)
    except AuthenticationException:
        sys.exit("Authentication failed. Check the credentials and try again.")
    except DivessiException as exc:
        sys.exit(f"Could not reach the API: {exc}")

    print("authenticated")

    if args.only:
        candidates = [item.strip() for item in args.only.split(",") if item.strip()]
    elif args.discover:
        candidates = list(DISCOVERY_CANDIDATES)
    else:
        candidates = list(KNOWN_COMMANDS)

    print(f"probing {len(candidates)} command(s) ...")

    marks = {
        "data": "  OK ",
        "empty": "  -- ",
        "unknown": "  .. ",
        "timeout": "  TO ",
        "error": "  !! ",
        "skipped": "  >> ",
    }

    files: Dict[str, Any] = {}
    with_data: List[str] = []
    media_urls: List[str] = []
    statuses: Dict[str, str] = {}

    def with_status(status: str) -> List[str]:
        return [w for w, s in statuses.items() if s == status]

    def build_manifest() -> Dict[str, Any]:
        return {
            "generated_utc": started.isoformat(),
            "account": username,
            "commands_probed": len(candidates),
            "commands_with_data": list(with_data),
            "commands_unknown": with_status("unknown"),
            "commands_skipped": with_status("skipped"),
            "commands_timeout": with_status("timeout"),
            "commands_error": with_status("error"),
            "files": dict(files),
            "media": None,
            "notes": [],
        }

    def write_manifest(manifest: Dict[str, Any]) -> None:
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def report(result: Dict[str, Any]) -> None:
        # Print and persist immediately, so a slow or interrupted run still
        # leaves everything fetched so far on disk, manifest included.
        mark = marks.get(result["status"], "  ?? ")
        print(f"{mark}{result['what']:<24} {result['detail'][:70]}", flush=True)
        statuses[result["what"]] = result["status"]
        if result["status"] == "data":
            what = result["what"]
            with_data.append(what)
            files[what] = dump_payload(what, result["payload"], out)
            media_urls.extend(harvest_urls(result["payload"]))
        write_manifest(build_manifest())

    results = discover(
        api, candidates, delay=args.delay, on_result=report, cooldown=args.cooldown
    )

    manifest = build_manifest()

    if not manifest["commands_with_data"]:
        manifest["notes"].append("no command returned data")

    if manifest["commands_skipped"]:
        why = ", ".join(
            sorted({results[w]["detail"] for w in manifest["commands_skipped"]})
        )
        manifest["notes"].append(
            f"some commands were skipped ({why}); rerun later to fetch them"
        )
        print(f"warning: some commands were skipped ({why}), rerun later")

    if manifest["commands_timeout"]:
        manifest["notes"].append("some commands timed out; rerun later to retry them")

    retry_later = manifest["commands_skipped"] + manifest["commands_timeout"]
    if retry_later:
        print("rerun later with: --only " + ",".join(retry_later))

    # Keep the original script's output so existing workflows still work,
    # derived from the payload already fetched rather than a second call.
    divelog_result = results.get("get_divelog")
    if divelog_result and divelog_result["status"] == "data":
        try:
            rows = curate_divelog(divelog_result["payload"])
            if rows:
                count = write_csv(rows, out / "divelog.csv")
                manifest["files"]["divelog.csv"] = {"rows": count}
                print(f"wrote divelog.csv with {count} dives")
            else:
                manifest["notes"].append("logbook is empty")
        except DivessiException as exc:
            manifest["notes"].append(f"curated divelog.csv failed: {exc}")

    if media_urls and not args.no_media:
        print(f"downloading {len(set(media_urls))} media file(s) ...")
        manifest["media"] = download_media(
            sorted(set(media_urls)),
            out / "media",
            api.session,
            any_host=args.media_any_host,
            delay=args.delay,
        )

    write_manifest(manifest)

    print(f"\ndone. {len(manifest['commands_with_data'])} command(s) returned data")
    print(f"output: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
