"""Assemble the firmwares.json manifest from GitHub release payloads."""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from firmware_list.files import MirrorFile
from firmware_list.providers.github import GitHubReleaseProvider

HashFetcher = Callable[[str, Path], str]


@dataclass(frozen=True)
class ManifestBuild:
    manifest: Dict[str, Any]
    mirror_files: List[MirrorFile]


def manifest_changed(
    old: Optional[Dict[str, Any]],
    new: Dict[str, Any],
) -> bool:
    """True when the manifest's substance changed.

    `refreshedAt` is a per-run timestamp and is ignored: when nothing else
    moved, callers should keep the committed file (and its history) as is.
    """
    if old is None:
        return True
    keys = (set(old) | set(new)) - {"refreshedAt"}
    return any(old.get(k) != new.get(k) for k in keys)


def upstream_url(manifest: Dict[str, Any], fw: Dict[str, Any]) -> str:
    """Rebuild the upstream GitHub Releases URL for a manifest record.

    The record's public `url` points at the local mirror, so anything
    needing the true source (incremental hash reuse, re-downloads)
    rebuilds the link from owner/repo (manifest top level) plus the
    record's releaseTag and filename.
    """
    return (
        f"https://github.com/{manifest['owner']}/{manifest['repo']}"
        f"/releases/download/{fw['releaseTag']}/{fw['filename']}"
    )


def pick_latest_releases(
    releases: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """From a `/releases` listing (newest first), keep the latest stable
    release and the latest pre-release, stable first."""
    stable = next((r for r in releases if not r.get("prerelease")), None)
    pre = next((r for r in releases if r.get("prerelease")), None)
    return [r for r in (stable, pre) if r is not None]


def build_manifest(
    sources: Sequence[Tuple[GitHubReleaseProvider, Sequence[Dict[str, Any]]]],
    previous: Optional[Dict[str, Any]],
    fetch_sha256: HashFetcher,
    host_repo: str,
    refreshed_at: str,
) -> ManifestBuild:
    """Build the public manifest and runtime mirror files for each provider.

    Provider order determines release and firmware order. Prior checksums
    are reused only for matching upstream URLs and sizes, with
    public mirror namespaces identifying each prior record's provider.
    Nerivec's legacy flat mirror URLs are also recognized during migration.
    Every provider must contribute firmware or the build fails.
    All kept assets must have distinct mirror paths before hashing begins.
    """
    if not sources:
        raise ValueError("no firmware providers selected")
    source_ids: set[str] = set()
    for provider, _ in sources:
        if provider.source_id in source_ids:
            raise ValueError(f"duplicate source_id: {provider.source_id}")
        source_ids.add(provider.source_id)

    primary = sources[0][0]
    public_prefix = f"https://raw.githubusercontent.com/{host_repo}/main/firmwares/"
    releases_meta: List[Dict[str, Any]] = []
    candidates: List[
        Tuple[GitHubReleaseProvider, List[Tuple[Dict[str, Any], Path, str]]]
    ] = []
    mirror_paths: set[Path] = set()
    for provider, releases in sources:
        if not releases:
            raise ValueError(f"no selected releases for provider {provider.source_id}")
        provider_candidates: List[Tuple[Dict[str, Any], Path, str]] = []
        for release in releases:
            releases_meta.append({
                "tag": release["tag_name"],
                "publishedAt": release["published_at"],
                "prerelease": bool(release.get("prerelease")),
            })
            for asset in release.get("assets", []):
                name = asset.get("name", "")
                try:
                    fields = provider.parse(name)
                except ValueError as exc:
                    print(f"warning: skipping asset {name!r}: {exc}",
                          file=sys.stderr)
                    continue
                relative_path = provider.mirror_path(name)
                url = asset["browser_download_url"]
                size = asset["size"]
                firmware: Dict[str, Any] = {
                    **fields,
                    "filename": name,
                    "url": f"{public_prefix}{relative_path.as_posix()}",
                    "size": size,
                    "releaseTag": release["tag_name"],
                    "prerelease": bool(release.get("prerelease")),
                }
                if not provider.keep(firmware):
                    continue
                if relative_path in mirror_paths:
                    raise ValueError(
                        f"duplicate mirror path for provider {provider.source_id}: "
                        f"{relative_path.as_posix()}"
                    )
                mirror_paths.add(relative_path)
                provider_candidates.append((firmware, relative_path, url))

        if not provider_candidates:
            raise ValueError(
                f"no firmware records survived for provider {provider.source_id} "
                "— refusing to write an empty manifest (releases may still be "
                "uploading, the filename convention changed, or the keep filter "
                "rejected everything)"
            )
        candidates.append((provider, provider_candidates))

    firmwares: List[Dict[str, Any]] = []
    mirror_files: List[MirrorFile] = []
    for provider, provider_candidates in candidates:
        provider_prefix = f"{public_prefix}{provider.source_id}/"
        prev_by_url: Dict[str, Dict[str, Any]] = {
            provider.upstream_url(fw["releaseTag"], fw["filename"]): fw
            for fw in (previous or {}).get("firmwares", [])
            if fw.get("url", "").startswith(provider_prefix) or (
                provider.source_id == "nerivec"
                and fw.get("url") == f"{public_prefix}{fw['filename']}"
            )
        }
        for firmware, relative_path, url in provider_candidates:
            prev = prev_by_url.get(url)
            if prev and prev.get("size") == firmware["size"] and prev.get("sha256"):
                sha256 = prev["sha256"]
            else:
                sha256 = fetch_sha256(url, relative_path)
            firmware["sha256"] = sha256
            firmwares.append(firmware)
            mirror_files.append(MirrorFile(relative_path, url, sha256))

    return ManifestBuild({
        "owner": primary.owner,
        "repo": primary.repo,
        "releases": releases_meta,
        "refreshedAt": refreshed_at,
        "count": len(firmwares),
        "firmwares": firmwares,
    }, mirror_files)
