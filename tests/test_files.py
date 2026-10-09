import hashlib
import os
import stat
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from firmware_list.files import MirrorFile, cached_download, prune_and_ensure

URL = "https://github.com/o/r/releases/download/t/foo_zigbee_ncp_1.0_115200_sw_flow.gbl"
FILENAME = URL.rsplit("/", 1)[-1]
RELATIVE_PATH = Path("vendor-a") / FILENAME


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_cached_download_writes_file_and_returns_hash(tmp_path):
    data = b"firmware-bytes"
    downloads = []

    def fake_download(url: str) -> bytes:
        downloads.append(url)
        return data

    digest = cached_download(URL, tmp_path, RELATIVE_PATH, fake_download)
    assert digest == sha256_bytes(data)
    assert (tmp_path / RELATIVE_PATH).read_bytes() == data
    assert downloads == [URL]


def test_cached_download_hits_disk_on_second_call(tmp_path):
    downloads = []

    def fake_download(url: str) -> bytes:
        downloads.append(url)
        return b"firmware-bytes"

    first = cached_download(URL, tmp_path, RELATIVE_PATH, fake_download)
    second = cached_download(URL, tmp_path, RELATIVE_PATH, fake_download)
    assert first == second
    assert len(downloads) == 1  # second call served from disk


def test_cached_download_keeps_same_basename_in_separate_namespaces(tmp_path):
    payloads = {
        "https://example.com/vendor-a/fw.gbl": b"vendor-a-firmware",
        "https://example.com/vendor-b/fw.gbl": b"vendor-b-firmware",
    }
    for source_id in ("vendor-a", "vendor-b"):
        url = f"https://example.com/{source_id}/fw.gbl"
        digest = cached_download(
            url, tmp_path, Path(source_id) / "fw.gbl", payloads.__getitem__
        )
        assert digest == sha256_bytes(payloads[url])
        assert (tmp_path / source_id / "fw.gbl").read_bytes() == payloads[url]


WINDOWS_ALIAS_PATHS = [
    "vendor/fw.gbl:stream",
    "vendor:stream/fw.gbl",
    "vendor/fw.gbl.",
    "vendor/fw.gbl ",
    "vendor./fw.gbl",
    "vendor /fw.gbl",
    *[f"vendor/fw{char}.gbl" for char in '<>:"|?*\x00\x01\x1f'],
    *[
        f"vendor/{name}{extension}"
        for name in (
            "CON", "PRN", "AUX", "NUL",
            *[f"COM{number}" for number in range(1, 10)],
            *[f"LPT{number}" for number in range(1, 10)],
            *[f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "¹²³"],
        )
        for extension in ("", ".gbl")
    ],
    "vendor/con.GBL",
    "vendor/Com1.firmware.gbl",
]

UNSAFE_PATHS = [
    ".",
    "vendor/..",
    "../fw.gbl",
    "/fw.gbl",
    "C:fw.gbl",
    "C:/fw.gbl",
    "C:\\fw.gbl",
    "\\fw.gbl",
    *WINDOWS_ALIAS_PATHS,
]


def guard_windows_alias_resolution(monkeypatch, root: Path) -> None:
    # Invalid device paths must never reach filesystem resolution or access.
    original_resolve = Path.resolve
    unsafe_paths = {root / Path(path) for path in WINDOWS_ALIAS_PATHS}

    def guarded_resolve(path: Path, *args, **kwargs):
        if path in unsafe_paths:
            pytest.fail("unsafe Windows filename reached filesystem resolution")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)


@pytest.mark.parametrize("relative_path", UNSAFE_PATHS)
def test_cached_download_rejects_unsafe_paths(tmp_path, relative_path, monkeypatch):
    if relative_path in WINDOWS_ALIAS_PATHS:
        guard_windows_alias_resolution(monkeypatch, tmp_path)
    downloads = []

    def fake_download(url: str) -> bytes:
        downloads.append(url)
        return b"firmware-bytes"

    with pytest.raises(ValueError):
        cached_download(URL, tmp_path, Path(relative_path), fake_download)
    assert downloads == []
    assert list(tmp_path.iterdir()) == []


def test_prune_and_ensure_removes_stale_and_downloads_missing(tmp_path):
    keep_file = tmp_path / RELATIVE_PATH
    keep_file.parent.mkdir()
    keep_file.write_bytes(b"current")
    stale = (
        tmp_path / "stale-source" / "nested"
        / "old_brand_zigbee_ncp_0.1_115200_sw_flow.gbl"
    )
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"obsolete")
    flat_stale = tmp_path / "old-flat.gbl"
    flat_stale.write_bytes(b"obsolete")

    missing_path = Path("vendor-b") / "bar_bootloader_2.0.gbl"
    missing_url = (
        "https://github.com/Nerivec/silabs-firmware-builder"
        "/releases/download/v2026.6.1-pre2/bar_bootloader_2.0.gbl"
    )
    files = [
        MirrorFile(RELATIVE_PATH, URL, sha256_bytes(b"current")),
        MirrorFile(missing_path, missing_url, sha256_bytes(b"bl-bytes")),
    ]
    downloaded = []

    def fake_download(url: str) -> bytes:
        downloaded.append(url)
        return b"bl-bytes"

    prune_and_ensure(files, tmp_path, fake_download)
    assert not stale.exists()
    assert not flat_stale.exists()
    assert not (tmp_path / "stale-source").exists()
    assert keep_file.read_bytes() == b"current"
    assert (tmp_path / missing_path).read_bytes() == b"bl-bytes"
    assert downloaded == [missing_url]


def test_prune_and_ensure_replaces_mismatched_file(tmp_path):
    path = tmp_path / RELATIVE_PATH
    path.parent.mkdir()
    path.write_bytes(b"tampered-or-outdated")
    explicit_url = "https://example.com/releases/firmware.bin"
    files = [MirrorFile(RELATIVE_PATH, explicit_url, sha256_bytes(b"current"))]
    downloaded = []

    def fake_download(url: str) -> bytes:
        downloaded.append(url)
        return b"current"

    prune_and_ensure(files, tmp_path, fake_download)
    assert path.read_bytes() == b"current"
    assert downloaded == [explicit_url]


def test_prune_and_ensure_noop_when_in_sync(tmp_path):
    path = tmp_path / RELATIVE_PATH
    path.parent.mkdir()
    path.write_bytes(b"current")
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    original_mtime = path.stat().st_mtime_ns
    files = [MirrorFile(RELATIVE_PATH, URL, sha256_bytes(b"current"))]
    prune_and_ensure(files, tmp_path, lambda url: (_ for _ in ()).throw(
        AssertionError("should not download when in sync")))
    assert path.read_bytes() == b"current"
    assert path.stat().st_mtime_ns == original_mtime


def test_prune_and_ensure_keeps_same_basename_in_separate_namespaces(tmp_path):
    payloads = {
        "https://example.com/vendor-a/fw.gbl": b"vendor-a-firmware",
        "https://example.com/vendor-b/fw.gbl": b"vendor-b-firmware",
    }
    files = [
        MirrorFile(Path(source_id) / "fw.gbl", url, sha256_bytes(payloads[url]))
        for source_id, url in zip(("vendor-a", "vendor-b"), payloads)
    ]
    prune_and_ensure(files, tmp_path, payloads.__getitem__)
    for item in files:
        assert (tmp_path / item.relative_path).read_bytes() == payloads[item.upstream_url]


@pytest.mark.parametrize("relative_path", UNSAFE_PATHS)
def test_prune_and_ensure_validates_all_paths_before_mutation(
    tmp_path, relative_path, monkeypatch
):
    if relative_path in WINDOWS_ALIAS_PATHS:
        guard_windows_alias_resolution(monkeypatch, tmp_path)
    stale = tmp_path / "stale.gbl"
    stale.write_bytes(b"obsolete")
    files = [
        MirrorFile(RELATIVE_PATH, URL, sha256_bytes(b"current")),
        MirrorFile(Path(relative_path), URL, sha256_bytes(b"current")),
    ]
    downloads = []

    def fake_download(url: str) -> bytes:
        downloads.append(url)
        return b"current"

    with pytest.raises(ValueError):
        prune_and_ensure(files, tmp_path, fake_download)
    assert stale.read_bytes() == b"obsolete"
    assert downloads == []
    assert not (tmp_path / RELATIVE_PATH).exists()


def test_prune_and_ensure_empty_plan_preserves_root(tmp_path):
    stale = tmp_path / "stale-source" / "nested" / "fw.gbl"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"obsolete")
    prune_and_ensure([], tmp_path, lambda url: b"unused")
    assert tmp_path.is_dir()
    assert list(tmp_path.iterdir()) == []


def make_symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name == "nt" and (
            isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 1314
        ):
            pytest.skip(f"Windows symlink creation is not permitted: {exc}")
        raise


@pytest.mark.parametrize("target_kind", ["kept", "stale", "dangling", "outside"])
@pytest.mark.parametrize("directory_link", [False, True])
def test_prune_and_ensure_unlinks_stale_symlink_without_following_target(
    tmp_path, target_kind, directory_link
):
    target = tmp_path / "target"
    if target_kind == "outside":
        target = tmp_path.parent / f"{tmp_path.name}-outside"
    target_file = target / "fw.gbl" if directory_link else target
    if target_kind != "dangling":
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_bytes(b"target-bytes")
    link = tmp_path / "stale-link"
    make_symlink(link, target, directory=directory_link)
    files = []
    if target_kind == "kept":
        files.append(MirrorFile(
            target_file.relative_to(tmp_path), URL, sha256_bytes(b"target-bytes")
        ))

    prune_and_ensure(files, tmp_path, lambda url: b"should-not-download")

    assert not os.path.lexists(link)
    if target_kind in ("kept", "outside"):
        assert target_file.read_bytes() == b"target-bytes"
    else:
        assert not target_file.exists()


@pytest.mark.parametrize("link_kind", ["file", "directory", "dangling"])
@pytest.mark.parametrize("operation", ["cached_download", "prune_and_ensure"])
def test_requested_symlink_paths_reject_before_mutation(tmp_path, link_kind, operation):
    stale = tmp_path / "stale.gbl"
    stale.write_bytes(b"obsolete")
    target = tmp_path / "target"
    if link_kind == "directory":
        target.mkdir()
        (target / "fw.gbl").write_bytes(b"current")
    elif link_kind == "file":
        target.write_bytes(b"current")
    link = tmp_path / "link"
    make_symlink(link, target, directory=link_kind == "directory")
    relative_path = Path("link/fw.gbl") if link_kind == "directory" else Path("link")
    downloads = []

    def fake_download(url: str) -> bytes:
        downloads.append(url)
        return b"current"

    with pytest.raises(ValueError):
        if operation == "cached_download":
            cached_download(URL, tmp_path, relative_path, fake_download)
        else:
            files = [
                MirrorFile(RELATIVE_PATH, URL, sha256_bytes(b"current")),
                MirrorFile(relative_path, URL, sha256_bytes(b"current")),
            ]
            prune_and_ensure(files, tmp_path, fake_download)
    assert stale.read_bytes() == b"obsolete"
    assert link.is_symlink()
    assert downloads == []
    assert not (tmp_path / RELATIVE_PATH).exists()
    if link_kind == "directory":
        assert (target / "fw.gbl").read_bytes() == b"current"
    elif link_kind == "file":
        assert target.read_bytes() == b"current"
    else:
        assert not target.exists()


@pytest.mark.parametrize("directory_link", [False, True])
def test_cached_download_rejects_components_reported_as_symlinks(
    tmp_path, monkeypatch, directory_link
):
    # Also exercise the boundary on Windows hosts lacking symlink permission.
    link = tmp_path / "link"
    if directory_link:
        link.mkdir()
        (link / "fw.gbl").write_bytes(b"current")
    else:
        link.write_bytes(b"current")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink",
        lambda path: path == link or original_is_symlink(path),
    )
    relative_path = Path("link/fw.gbl") if directory_link else Path("link")
    with pytest.raises(ValueError):
        cached_download(URL, tmp_path, relative_path, lambda url: b"unexpected")


def test_pruning_unlinks_entry_reported_as_symlink_without_resolving_it(
    tmp_path, monkeypatch
):
    target = tmp_path / "kept.gbl"
    target.write_bytes(b"current")
    link = tmp_path / "link"
    link.write_bytes(b"alias-entry")
    original_is_symlink = Path.is_symlink
    original_resolve = Path.resolve
    monkeypatch.setattr(
        Path, "is_symlink",
        lambda path: path == link or original_is_symlink(path),
    )
    monkeypatch.setattr(
        Path, "resolve",
        lambda path, *args, **kwargs: (
            target if path == link else original_resolve(path, *args, **kwargs)
        ),
    )
    files = [MirrorFile(Path("kept.gbl"), URL, sha256_bytes(b"current"))]
    prune_and_ensure(files, tmp_path, lambda url: b"unexpected")
    assert not link.exists()
    assert target.read_bytes() == b"current"


@pytest.mark.parametrize("directory_entry", [False, True])
def test_requested_reparse_point_rejects_before_mutation(
    tmp_path, monkeypatch, directory_entry
):
    stale = tmp_path / "stale.gbl"
    stale.write_bytes(b"obsolete")
    reparse = tmp_path / "reparse"
    if directory_entry:
        reparse.mkdir()
        (reparse / "fw.gbl").write_bytes(b"current")
    else:
        reparse.write_bytes(b"current")
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == reparse:
            return SimpleNamespace(
                st_file_attributes=0x400,
                st_mode=stat.S_IFDIR if directory_entry else stat.S_IFREG,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    # Ordinary symlink detection cannot identify a Windows junction.
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    relative = Path("reparse/fw.gbl") if directory_entry else Path("reparse")
    files = [
        MirrorFile(RELATIVE_PATH, URL, sha256_bytes(b"current")),
        MirrorFile(relative, URL, sha256_bytes(b"current")),
    ]
    downloads = []
    with pytest.raises(ValueError):
        prune_and_ensure(files, tmp_path, lambda url: downloads.append(url) or b"current")
    assert stale.read_bytes() == b"obsolete"
    assert downloads == []
    assert not (tmp_path / RELATIVE_PATH).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_pruning_removes_ancestor_junction_without_following_target(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    firmware = vendor / "fw.gbl"
    firmware.write_bytes(b"current")
    junction = vendor / "loop"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(vendor)],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        script = (
            "from pathlib import Path; "
            "from firmware_list.files import MirrorFile, prune_and_ensure; "
            "prune_and_ensure([MirrorFile(Path('vendor/fw.gbl'), "
            f"{URL!r}, {sha256_bytes(b'current')!r})], "
            f"Path({str(tmp_path)!r}), lambda url: b'unexpected')"
        )
        pruned = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=3,
        )
        assert pruned.returncode == 0, pruned.stdout + pruned.stderr
        assert not os.path.lexists(junction)
        assert firmware.read_bytes() == b"current"
    finally:
        if os.path.lexists(junction):
            junction.rmdir()


def test_pruning_removes_directory_reparse_entry_without_resolving_target(
    tmp_path, monkeypatch
):
    target = tmp_path / "kept.gbl"
    target.write_bytes(b"current")
    reparse = tmp_path / "reparse"
    reparse.mkdir()
    original_lstat = Path.lstat
    original_resolve = Path.resolve

    def reparse_lstat(path: Path):
        if path == reparse:
            return SimpleNamespace(st_file_attributes=0x400, st_mode=stat.S_IFDIR)
        return original_lstat(path)

    def guarded_resolve(path: Path, *args, **kwargs):
        assert path != reparse, "reparse target must not be resolved"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    files = [MirrorFile(Path("kept.gbl"), URL, sha256_bytes(b"current"))]
    prune_and_ensure(files, tmp_path, lambda url: b"unexpected")
    assert not reparse.exists()
    assert target.read_bytes() == b"current"


def exercise_root_operation(operation: str, root: Path, download) -> None:
    if operation == "cached_download":
        cached_download(URL, root, Path("fw.gbl"), download)
    else:
        files = [] if operation == "prune_empty" else [
            MirrorFile(Path("fw.gbl"), URL, sha256_bytes(b"current"))
        ]
        prune_and_ensure(files, root, download)


@pytest.mark.parametrize("link_kind", ["symlink", "reparse"])
@pytest.mark.parametrize("operation", ["cached_download", "prune_empty", "prune_files"])
def test_link_like_mirror_root_rejects_before_touching_target(
    tmp_path, monkeypatch, link_kind, operation
):
    root = tmp_path / "mirror"
    root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    sentinel = target / "sentinel.gbl"
    sentinel.write_bytes(b"outside-sentinel")
    original_lstat = Path.lstat
    original_is_symlink = Path.is_symlink
    original_resolve = Path.resolve

    def root_lstat(path: Path):
        if path == root:
            return SimpleNamespace(st_file_attributes=0x400, st_mode=stat.S_IFDIR)
        return original_lstat(path)

    monkeypatch.setattr(Path, "resolve", lambda path, *args, **kwargs: (
        target if path == root else original_resolve(path, *args, **kwargs)
    ))
    if link_kind == "reparse":
        monkeypatch.setattr(Path, "lstat", root_lstat)
    else:
        monkeypatch.setattr(Path, "is_symlink", lambda path: (
            path == root or original_is_symlink(path)
        ))
    downloads = []
    with pytest.raises(ValueError, match="root"):
        exercise_root_operation(operation, root, lambda url: downloads.append(url) or b"current")
    assert downloads == []
    assert sentinel.read_bytes() == b"outside-sentinel"
    assert not (target / "fw.gbl").exists()


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
@pytest.mark.parametrize("operation", ["cached_download", "prune_empty", "prune_files"])
def test_real_link_like_mirror_root_preserves_outside_target(tmp_path, link_kind, operation):
    root = tmp_path / "mirror"
    target = tmp_path / "outside"
    target.mkdir()
    sentinel = target / "sentinel.gbl"
    sentinel.write_bytes(b"outside-sentinel")
    if link_kind == "symlink":
        make_symlink(root, target, directory=True)
    else:
        if os.name != "nt":
            pytest.skip("Windows junction regression")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(root), str(target)],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    try:
        downloads = []
        with pytest.raises(ValueError, match="root"):
            exercise_root_operation(operation, root, lambda url: downloads.append(url) or b"current")
        assert downloads == []
        assert sentinel.read_bytes() == b"outside-sentinel"
        assert not (target / "fw.gbl").exists()
    finally:
        if os.path.lexists(root):
            if link_kind == "junction":
                root.rmdir()
            else:
                root.unlink()


@pytest.mark.parametrize("old_bytes", [None, b"old-firmware"])
def test_bad_replacement_checksum_never_writes_downloaded_bytes(tmp_path, old_bytes):
    relative_path = Path("vendor") / "fw.gbl"
    path = tmp_path / relative_path
    if old_bytes is not None:
        path.parent.mkdir()
        path.write_bytes(old_bytes)
    files = [MirrorFile(relative_path, URL, sha256_bytes(b"expected-firmware"))]
    downloads = []

    def fake_download(url: str) -> bytes:
        downloads.append(url)
        return b"incorrect-download"

    with pytest.raises(ValueError, match="[Ss][Hh][Aa]-?256|checksum"):
        prune_and_ensure(files, tmp_path, fake_download)
    assert downloads == [URL]
    if old_bytes is None:
        assert not path.exists()
        assert not path.parent.exists()
    else:
        assert path.read_bytes() == old_bytes
