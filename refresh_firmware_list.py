"""Regenerate firmwares.json and mirror firmware files from the latest
stable + pre-release of each registered provider.

Provider listings are collected before updating the mirror. On any failure
this script exits non-zero without rewriting firmwares.json.

GITHUB_TOKEN is optional; when set (as in CI) it lifts the GitHub API rate
limit from 60/h to 1000/h.
"""

import datetime as dt
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from firmware_list.files import ByteDownloader, cached_download, prune_and_ensure
from firmware_list.manifest import (
    build_manifest,
    manifest_changed,
    pick_latest_releases,
)
from firmware_list.providers import PROVIDERS
from firmware_list.providers.github import GitHubReleaseProvider

JsonFetcher = Callable[[str], Any]

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "firmwares.json"
FIRMWARES_DIR = ROOT / "firmwares"
# This repository, "owner/name" — CI sets GITHUB_REPOSITORY automatically.
HOST_REPO = os.environ.get("GITHUB_REPOSITORY", "SONOFF-Technologies/sonoff-dongle-series-open-source-firmware")
USER_AGENT = "sonoff-dongle-series-open-source-firmware-sync"


class NoReleasesError(ValueError):
    """A provider has no stable or prerelease release to select."""


def api_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=api_headers())
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def fetch_bytes(
    url: str, attempts: int = 3, backoff_s: int = 3
) -> bytes:
    """Download `url` fully, retrying transient network errors."""
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(backoff_s * attempt)
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                data: bytes = resp.read()
                return data
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
    raise RuntimeError(
        f"failed to download {url} after {attempts} attempts"
    ) from last_error


def load_previous() -> Optional[Dict[str, Any]]:
    if OUTPUT.exists():
        previous: Dict[str, Any] = json.loads(
            OUTPUT.read_text(encoding="utf-8")
        )
        return previous
    return None


def refresh(
    providers: Sequence[GitHubReleaseProvider],
    previous: Optional[Dict[str, Any]],
    fetch_json: JsonFetcher,
    fetch_bytes: ByteDownloader,
    host_repo: str,
    firmwares_dir: Path,
    refreshed_at: str,
) -> Dict[str, Any]:
    """Fetch all provider listings before building and converging the mirror."""
    sources: List[Tuple[GitHubReleaseProvider, Sequence[Dict[str, Any]]]] = []
    for provider in providers:
        releases = pick_latest_releases(fetch_json(provider.api_url))
        if not releases:
            raise NoReleasesError(
                f"no releases found for provider {provider.source_id}"
            )
        sources.append((provider, releases))
    result = build_manifest(
        sources,
        previous,
        lambda url, relative_path: cached_download(
            url, firmwares_dir, relative_path, fetch_bytes,
            reuse_existing=False,
        ),
        host_repo=host_repo,
        refreshed_at=refreshed_at,
    )
    prune_and_ensure(result.mirror_files, firmwares_dir, fetch_bytes)
    return result.manifest


def main() -> int:
    previous = load_previous()
    refreshed_at = dt.datetime.now(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        manifest = refresh(
            PROVIDERS, previous, http_get_json, fetch_bytes,
            HOST_REPO, FIRMWARES_DIR, refreshed_at,
        )
    except NoReleasesError:
        print("error: no releases found upstream", file=sys.stderr)
        return 1

    if not manifest_changed(previous, manifest):
        tags = ", ".join(r["tag"] for r in manifest["releases"])
        print(f"no change (except refreshedAt) — keeping {tags}")
        return 0
    OUTPUT.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    tags = ", ".join(r["tag"] for r in manifest["releases"])
    print(f"wrote {manifest['count']} firmwares from {tags}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
