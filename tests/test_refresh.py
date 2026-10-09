import hashlib
import json

import pytest

import refresh_firmware_list as refresh_module
from firmware_list.providers.github import GitHubReleaseProvider
from refresh_firmware_list import refresh


HOST_REPO = "host/firmware-list"
REFRESHED_AT = "2026-08-01T12:00:00Z"


def parse_shared(name):
    return {
        "brand": "sonoff", "model": "dongle", "type": "bootloader",
        "version": "1.0.0",
    }


def keep_all(firmware):
    return True


def providers():
    return (
        GitHubReleaseProvider("vendor-a", "owner-a", "repo-a",
                              parse_shared, keep_all),
        GitHubReleaseProvider("vendor-b", "owner-b", "repo-b",
                              parse_shared, keep_all),
    )


def release(provider, tag, data):
    return {
        "tag_name": tag,
        "published_at": REFRESHED_AT,
        "prerelease": False,
        "assets": [{
            "name": "shared.gbl",
            "browser_download_url": provider.upstream_url(tag, "shared.gbl"),
            "size": len(data),
        }],
    }


def transports(sources):
    first, second = sources
    payloads = {
        first.upstream_url("a1", "shared.gbl"): b"first firmware",
        second.upstream_url("b1", "shared.gbl"): b"second firmware",
    }
    listings = {
        provider.api_url: [release(provider, tag, payloads[url])]
        for provider, tag, url in zip(sources, ("a1", "b1"), payloads)
    }
    json_requests = []
    byte_requests = []

    def fetch_json(url):
        json_requests.append(url)
        return listings[url]

    def fetch_bytes(url):
        # All listings must succeed before hashing or mirroring begins.
        assert json_requests == [provider.api_url for provider in sources]
        byte_requests.append(url)
        return payloads[url]

    return listings, payloads, json_requests, byte_requests, fetch_json, fetch_bytes


def test_refresh_collects_providers_in_order_and_mirrors_shared_filenames(tmp_path):
    sources = providers()
    _, payloads, json_requests, byte_requests, fetch_json, fetch_bytes = transports(sources)
    mirror = tmp_path / "firmwares"
    mirror.mkdir()
    (mirror / "stale.gbl").write_bytes(b"stale")

    manifest = refresh(sources, None, fetch_json, fetch_bytes,
                       HOST_REPO, mirror, REFRESHED_AT)

    assert json_requests == [provider.api_url for provider in sources]
    assert byte_requests == list(payloads)
    assert manifest["count"] == 2
    assert (manifest["owner"], manifest["repo"]) == ("owner-a", "repo-a")
    assert manifest["refreshedAt"] == REFRESHED_AT
    assert [item["tag"] for item in manifest["releases"]] == ["a1", "b1"]
    for provider, firmware, data in zip(sources, manifest["firmwares"], payloads.values()):
        assert firmware["url"] == (
            f"https://raw.githubusercontent.com/{HOST_REPO}/main/firmwares/"
            f"{provider.source_id}/shared.gbl"
        )
        assert firmware["sha256"] == hashlib.sha256(data).hexdigest()
        assert (mirror / provider.source_id / "shared.gbl").read_bytes() == data
    assert sorted(path.relative_to(mirror).as_posix()
                  for path in mirror.rglob("*") if path.is_file()) == [
        "vendor-a/shared.gbl", "vendor-b/shared.gbl",
    ]
    assert not (tmp_path / "firmwares.json").exists()


def test_refresh_replaces_existing_file_when_release_url_changes(tmp_path):
    provider = providers()[0]
    mirror = tmp_path / "firmwares"
    old_data = b"old firmware"
    new_data = b"new firmware"
    listing = [release(provider, "a1", old_data)]
    payloads = {
        provider.upstream_url("a1", "shared.gbl"): old_data,
        provider.upstream_url("a2", "shared.gbl"): new_data,
    }
    byte_requests = []

    def fetch_json(url):
        assert url == provider.api_url
        return listing

    def fetch_bytes(url):
        byte_requests.append(url)
        return payloads[url]

    previous = refresh((provider,), None, fetch_json, fetch_bytes,
                       HOST_REPO, mirror, REFRESHED_AT)
    byte_requests.clear()
    listing[:] = [release(provider, "a2", new_data)]

    manifest = refresh((provider,), previous, fetch_json, fetch_bytes,
                       HOST_REPO, mirror, REFRESHED_AT)

    new_url = provider.upstream_url("a2", "shared.gbl")
    assert byte_requests == [new_url]
    assert manifest["firmwares"][0]["releaseTag"] == "a2"
    assert manifest["firmwares"][0]["sha256"] == hashlib.sha256(new_data).hexdigest()
    assert (mirror / provider.source_id / "shared.gbl").read_bytes() == new_data


def test_empty_later_provider_preserves_mirror_without_downloads(tmp_path):
    sources = providers()
    listings, _, json_requests, byte_requests, fetch_json, fetch_bytes = transports(sources)
    listings[sources[1].api_url] = []
    sentinel = tmp_path / "sentinel.gbl"
    sentinel.write_bytes(b"preserve me")

    with pytest.raises(ValueError, match="vendor-b"):
        refresh(sources, None, fetch_json, fetch_bytes,
                HOST_REPO, tmp_path, REFRESHED_AT)

    assert json_requests == [provider.api_url for provider in sources]
    assert byte_requests == []
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve me"


def test_later_listing_fetch_failure_precedes_hash_and_mirror_writes(tmp_path):
    sources = providers()
    listings, _, json_requests, byte_requests, _, fetch_bytes = transports(sources)
    sentinel = tmp_path / "sentinel.gbl"
    sentinel.write_bytes(b"preserve me")
    error = OSError("provider API unavailable")

    def fetch_json(url):
        json_requests.append(url)
        if url == sources[1].api_url:
            raise error
        return listings[url]

    with pytest.raises(OSError) as caught:
        refresh(sources, None, fetch_json, fetch_bytes,
                HOST_REPO, tmp_path, REFRESHED_AT)

    assert caught.value is error
    assert json_requests == [provider.api_url for provider in sources]
    assert byte_requests == []
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve me"


def test_main_does_not_rewrite_timestamp_only_change(tmp_path, monkeypatch, capsys):
    sources = providers()
    _, _, json_requests, byte_requests, fetch_json, fetch_bytes = transports(sources)
    mirror = tmp_path / "firmwares"
    previous = refresh(sources, None, fetch_json, fetch_bytes,
                       HOST_REPO, mirror, REFRESHED_AT)
    output = tmp_path / "firmwares.json"
    original = json.dumps(previous, indent=4) + "\n"
    output.write_text(original, encoding="utf-8")
    original_mtime = output.stat().st_mtime_ns
    json_requests.clear()
    byte_requests.clear()
    load_calls = []
    real_load_previous = refresh_module.load_previous

    def load_previous():
        load_calls.append(True)
        return real_load_previous()

    monkeypatch.setattr(refresh_module, "PROVIDERS", sources, raising=False)
    monkeypatch.setattr(refresh_module, "OUTPUT", output)
    monkeypatch.setattr(refresh_module, "FIRMWARES_DIR", mirror)
    monkeypatch.setattr(refresh_module, "HOST_REPO", HOST_REPO)
    monkeypatch.setattr(refresh_module, "http_get_json", fetch_json)
    monkeypatch.setattr(refresh_module, "fetch_bytes", fetch_bytes)
    monkeypatch.setattr(refresh_module, "load_previous", load_previous)

    assert refresh_module.main() == 0
    assert load_calls == [True]
    assert json_requests == [provider.api_url for provider in sources]
    assert byte_requests == []
    assert output.read_text(encoding="utf-8") == original
    assert output.stat().st_mtime_ns == original_mtime
    assert "no change (except refreshedAt) — keeping a1, b1" in capsys.readouterr().out


def test_main_reports_empty_provider_with_original_cli_status(tmp_path, monkeypatch, capsys):
    sources = providers()
    listings, _, json_requests, byte_requests, fetch_json, fetch_bytes = transports(sources)
    listings[sources[1].api_url] = []
    mirror = tmp_path / "firmwares"
    mirror.mkdir()
    sentinel = mirror / "sentinel.gbl"
    sentinel.write_bytes(b"preserve me")
    output = tmp_path / "firmwares.json"
    original = '{"count": 1}\n'
    output.write_text(original, encoding="utf-8")
    monkeypatch.setattr(refresh_module, "PROVIDERS", sources)
    monkeypatch.setattr(refresh_module, "OUTPUT", output)
    monkeypatch.setattr(refresh_module, "FIRMWARES_DIR", mirror)
    monkeypatch.setattr(refresh_module, "http_get_json", fetch_json)
    monkeypatch.setattr(refresh_module, "fetch_bytes", fetch_bytes)

    assert refresh_module.main() == 1

    captured = capsys.readouterr()
    assert captured.err == "error: no releases found upstream\n"
    assert captured.out == ""
    assert json_requests == [provider.api_url for provider in sources]
    assert byte_requests == []
    assert output.read_text(encoding="utf-8") == original
    assert list(mirror.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve me"


def test_main_propagates_unrelated_manifest_value_error(tmp_path, monkeypatch, capsys):
    sources = providers()
    listings, _, _, byte_requests, fetch_json, fetch_bytes = transports(sources)
    listings[sources[0].api_url][0]["assets"] = []
    output = tmp_path / "firmwares.json"
    monkeypatch.setattr(refresh_module, "PROVIDERS", sources)
    monkeypatch.setattr(refresh_module, "OUTPUT", output)
    monkeypatch.setattr(refresh_module, "FIRMWARES_DIR", tmp_path / "firmwares")
    monkeypatch.setattr(refresh_module, "http_get_json", fetch_json)
    monkeypatch.setattr(refresh_module, "fetch_bytes", fetch_bytes)

    with pytest.raises(ValueError, match="no firmware records survived.*vendor-a"):
        refresh_module.main()

    assert byte_requests == []
    assert not output.exists()
    assert capsys.readouterr().err == ""
