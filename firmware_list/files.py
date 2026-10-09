"""Mirror firmware files into the repo's firmwares/ directory.

The manifest's `url` points at these mirrored files on
raw.githubusercontent.com (CORS-enabled), so a web frontend can fetch
the bytes directly for flashing. The directory converges to exactly the
current manifest: stale files are pruned, missing or
mismatched files are (re-)downloaded, unchanged files are never touched.
"""

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable, Sequence

ByteDownloader = Callable[[str], bytes]
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "¹²³"),
}


@dataclass(frozen=True)
class MirrorFile:
    relative_path: Path
    upstream_url: str
    sha256: str


def _is_link_like(path: Path) -> bool:
    """Detect symlinks and Windows reparse entries without following targets."""
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _unlink_link_like(path: Path) -> None:
    """Remove the link entry; directory junctions require rmdir on Windows."""
    if stat.S_ISDIR(path.lstat().st_mode):
        path.rmdir()
    else:
        path.unlink()


def _resolve_mirror_root(directory: Path) -> Path:
    """Reject link-like mirror roots before resolving their targets."""
    if _is_link_like(directory):
        raise ValueError(f"mirror root must not be a link-like entry: {directory}")
    return directory.resolve()


def _resolve_mirror_path(directory: Path, relative_path: Path) -> Path:
    """Resolve a mirror path without allowing it to escape its directory."""
    windows_path = PureWindowsPath(relative_path)
    if relative_path.is_absolute() or windows_path.drive or windows_path.root:
        raise ValueError(f"mirror path must be relative: {relative_path}")
    for component in relative_path.parts:
        if component == "..":
            continue  # The resolved containment check handles parent traversal.
        basename = component.split(".", 1)[0].rstrip(" ").upper()
        if (
            component.endswith((".", " "))
            or basename in _WINDOWS_RESERVED_NAMES
            or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in component)
        ):
            raise ValueError(f"mirror path has an unsafe filename: {relative_path}")
    root = _resolve_mirror_root(directory)
    component_path = root
    for component in relative_path.parts:
        component_path = component_path / component
        if _is_link_like(component_path):
            raise ValueError(f"mirror path traverses a link-like entry: {relative_path}")
    path = (root / relative_path).resolve()
    if path == root:
        raise ValueError("mirror path must name a file within the directory")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"mirror path escapes directory: {relative_path}") from exc
    return path


def cached_download(
    url: str,
    directory: Path,
    relative_path: Path,
    download: ByteDownloader,
    *,
    reuse_existing: bool = True,
) -> str:
    """Return the sha256 of the firmware at `url`, mirroring it on disk.

    When `reuse_existing` is true and the file is already mirrored at
    `relative_path` within `directory`, hash the local bytes without using the
    network. Otherwise download via `download(url)` and replace the file.
    """
    path = _resolve_mirror_path(directory, relative_path)
    if reuse_existing and path.exists():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    data = download(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def prune_and_ensure(
    files: Sequence[MirrorFile],
    directory: Path,
    download: ByteDownloader,
) -> None:
    """Converge `directory` to exactly the requested mirror files.

    Validates all requested paths before deleting stale files, (re-)downloads any
    record whose file is missing or whose on-disk bytes hash to something
    other than the requested sha256, and leaves matching files alone.
    Downloads come from the upstream release, not the mirror.
    """
    root = _resolve_mirror_root(directory)
    resolved_files = [
        (item, _resolve_mirror_path(root, item.relative_path))
        for item in files
    ]
    keep = {path for _, path in resolved_files}

    pending_directories = [root] if root.exists() else []
    directories = []
    while pending_directories:
        parent = pending_directories.pop()
        for candidate in parent.iterdir():
            # A stale link is an entry to unlink, never a target to resolve.
            if _is_link_like(candidate):
                _unlink_link_like(candidate)
                continue
            path = _resolve_mirror_path(root, candidate.relative_to(root))
            if path.is_dir():
                directories.append(path)
                pending_directories.append(path)
            elif path.is_file() and path not in keep:
                path.unlink()

    for item, path in resolved_files:
        if not path.exists() or (
            hashlib.sha256(path.read_bytes()).hexdigest() != item.sha256
        ):
            data = download(item.upstream_url)
            actual_sha256 = hashlib.sha256(data).hexdigest()
            if actual_sha256 != item.sha256:
                raise ValueError(
                    f"download SHA-256 mismatch for {item.relative_path}: "
                    f"expected {item.sha256}, got {actual_sha256}"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    for candidate in sorted(
        directories, key=lambda path: len(path.parts), reverse=True
    ):
        if _is_link_like(candidate):
            _unlink_link_like(candidate)
            continue
        path = _resolve_mirror_path(root, candidate.relative_to(root))
        if path != root and path.is_dir() and not any(path.iterdir()):
            path.rmdir()
