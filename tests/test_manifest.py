import copy
from dataclasses import replace
from pathlib import Path

import pytest

from firmware_list.manifest import build_manifest, manifest_changed
from firmware_list.providers.github import GitHubReleaseProvider
from firmware_list.providers.nerivec import NERIVEC


def keep_all(firmware):
    return True

RELEASE = {
    "tag_name": "v2025.6.2-update1",
    "published_at": "2026-08-01T12:00:00Z",
    "prerelease": False,
    "assets": [
        {
            "name": "sonoff_dongle-pmg24_zigbee_ncp_8.2.2.0_460800_sw_flow.gbl",
            "browser_download_url": (
                "https://github.com/Nerivec/silabs-firmware-builder/"
                "releases/download/v2025.6.2-update1/"
                "sonoff_dongle-pmg24_zigbee_ncp_8.2.2.0_460800_sw_flow.gbl"
            ),
            "size": 245760,
        },
        {
            "name": "nabucasa_skyconnect_bootloader_3.1.2.gbl",
            "browser_download_url": (
                "https://github.com/Nerivec/silabs-firmware-builder/"
                "releases/download/v2025.6.2-update1/"
                "nabucasa_skyconnect_bootloader_3.1.2.gbl"
            ),
            "size": 231456,
        },
    ],
}

PRE_RELEASE = {
    "tag_name": "v2026.6.1-pre1",
    "published_at": "2026-08-10T12:00:00Z",
    "prerelease": True,
    "assets": [
        {
            "name": "sonoff_dongle-pmg24_zigbee_ncp_8.2.2.1_460800_sw_flow.gbl",
            "browser_download_url": (
                "https://github.com/Nerivec/silabs-firmware-builder/"
                "releases/download/v2026.6.1-pre1/"
                "sonoff_dongle-pmg24_zigbee_ncp_8.2.2.1_460800_sw_flow.gbl"
            ),
            "size": 245999,
        },
    ],
}


class Recorder:
    """Fake hash fetcher that records which urls it was asked to hash."""

    def __init__(self) -> None:
        self.fetched: list = []

    def __call__(self, url: str, relative_path: Path) -> str:
        self.fetched.append((url, relative_path))
        return f"hash-of-{url}"


def build(releases=None, previous=None, recorder=None, **overrides):
    kwargs = dict(
        host_repo="SONOFF-Technologies/sonoff-dongle-series-open-source-firmware",
        refreshed_at="2026-08-14T07:07:00Z",
    )
    provider = replace(NERIVEC, keep=overrides.pop("keep", keep_all))
    kwargs.update(overrides)
    return build_manifest(
        [(provider, releases if releases is not None else [RELEASE])],
        previous,
        recorder or Recorder(),
        **kwargs,
    ).manifest


def stale_previous():
    return build(recorder=Recorder(), refreshed_at="2026-08-14T06:07:00Z")


def test_builds_manifest_for_new_release():
    recorder = Recorder()
    manifest = build(recorder=recorder)
    assert manifest["owner"] == "Nerivec"
    assert manifest["repo"] == "silabs-firmware-builder"
    assert manifest["refreshedAt"] == "2026-08-14T07:07:00Z"
    assert manifest["count"] == 2
    assert manifest["releases"] == [
        {
            "tag": "v2025.6.2-update1",
            "publishedAt": "2026-08-01T12:00:00Z",
            "prerelease": False,
        }
    ]

    first = manifest["firmwares"][0]
    assert first["brand"] == "sonoff"
    assert first["model"] == "dongle-pmg24"
    assert first["type"] == "zigbee_ncp"
    assert first["version"] == "8.2.2.0"
    assert first["baudRate"] == 460800
    assert first["flowControl"] == "sw_flow"
    assert first["filename"] == RELEASE["assets"][0]["name"]
    assert first["url"] == (
        "https://raw.githubusercontent.com/SONOFF-Technologies/sonoff-dongle-series-open-source-firmware/"
        "main/firmwares/nerivec/"
        "sonoff_dongle-pmg24_zigbee_ncp_8.2.2.0_460800_sw_flow.gbl"
    )
    assert "downloadUrl" not in first
    assert first["size"] == 245760
    # hash fetcher is keyed on the UPSTREAM asset url, not the mirror url
    assert first["sha256"] == (
        f"hash-of-{RELEASE['assets'][0]['browser_download_url']}"
    )

    # new release, no previous manifest: every firmware must be hashed
    assert len(recorder.fetched) == 2
    assert recorder.fetched[0] == (
        RELEASE["assets"][0]["browser_download_url"],
        Path("nerivec", RELEASE["assets"][0]["name"]),
    )


def test_reuses_sha256_when_url_and_size_unchanged():
    previous = stale_previous()
    old_hashes = {fw["url"]: fw["sha256"] for fw in previous["firmwares"]}
    recorder = Recorder()
    manifest = build(previous=previous, recorder=recorder)
    # hourly rerun against the same release: nothing downloaded
    assert recorder.fetched == []
    assert all(
        fw["sha256"] == old_hashes[fw["url"]] for fw in manifest["firmwares"]
    )
    assert manifest["refreshedAt"] == "2026-08-14T07:07:00Z"


def test_rehashes_when_size_changes():
    rerelease = copy.deepcopy(RELEASE)
    rerelease["assets"][0]["size"] = 999999  # asset replaced under same url

    recorder = Recorder()
    manifest = build(releases=[rerelease], previous=stale_previous(),
                     recorder=recorder)
    changed = manifest["firmwares"][0]
    unchanged = manifest["firmwares"][1]
    assert changed["size"] == 999999
    assert changed["sha256"] == (
        f"hash-of-{RELEASE['assets'][0]['browser_download_url']}"
    )
    assert len(recorder.fetched) == 1


def test_refuses_when_no_asset_parses():
    broken = copy.deepcopy(RELEASE)
    for asset in broken["assets"]:
        asset["name"] = "totally_unknown_convention.gbl"
    with pytest.raises(ValueError):
        build(releases=[broken])


def test_refuses_when_release_has_no_assets():
    with pytest.raises(ValueError):
        build(releases=[{"tag_name": "t", "published_at": "p",
                         "assets": []}])


def test_partial_failure_keeps_good_assets_and_warns(capsys):
    mixed = copy.deepcopy(RELEASE)
    mixed["assets"][1]["name"] = "totally_unknown_convention.gbl"
    manifest = build(releases=[mixed])
    assert manifest["count"] == 1
    assert manifest["firmwares"][0]["type"] == "zigbee_ncp"
    assert "warning" in capsys.readouterr().err


def test_keep_filter_restricts_manifest_and_reuses_hashes():
    from firmware_list.providers.nerivec import is_sonoff_dongle

    recorder = Recorder()
    manifest = build(previous=stale_previous(), recorder=recorder,
                     keep=is_sonoff_dongle)
    # only the sonoff dongle record survives; bootloader (nabucasa) is out
    assert manifest["count"] == 1
    assert manifest["firmwares"][0]["brand"] == "sonoff"
    assert manifest["firmwares"][0]["model"] == "dongle-pmg24"
    # and its hash was still reused from the previous manifest
    assert recorder.fetched == []


def test_keep_filter_rejects_manifest_when_everything_filtered_out():
    def keep_nothing(fw):
        return False

    with pytest.raises(ValueError):
        build(keep=keep_nothing)


def test_includes_latest_pre_release_flagged_on_records():
    recorder = Recorder()
    manifest = build(releases=[RELEASE, PRE_RELEASE], recorder=recorder)
    assert manifest["count"] == 3
    assert manifest["releases"] == [
        {
            "tag": "v2025.6.2-update1",
            "publishedAt": "2026-08-01T12:00:00Z",
            "prerelease": False,
        },
        {
            "tag": "v2026.6.1-pre1",
            "publishedAt": "2026-08-10T12:00:00Z",
            "prerelease": True,
        },
    ]
    stable = manifest["firmwares"][0]
    pre = manifest["firmwares"][2]
    assert stable["prerelease"] is False
    assert stable["releaseTag"] == "v2025.6.2-update1"
    assert pre["prerelease"] is True
    assert pre["releaseTag"] == "v2026.6.1-pre1"
    assert pre["version"] == "8.2.2.1"
    # both releases' firmwares were hashed (different urls)
    assert len(recorder.fetched) == 3


def test_pre_release_assets_share_no_hash_with_stable():
    # same url+size across releases reuses the hash (url is the key)
    same_asset = copy.deepcopy(PRE_RELEASE)
    same_asset["assets"][0]["browser_download_url"] = (
        RELEASE["assets"][0]["browser_download_url"]
    )
    same_asset["assets"][0]["size"] = RELEASE["assets"][0]["size"]
    previous = build(releases=[RELEASE])  # hashes it as stable
    recorder = Recorder()
    manifest = build(releases=[RELEASE, same_asset], previous=previous,
                     recorder=recorder)
    assert recorder.fetched == []  # reused
    assert manifest["firmwares"][2]["prerelease"] is True


def test_sonoff_dongle_filter_accepts_zbdongle_e_spelling():
    from firmware_list.providers.nerivec import is_sonoff_dongle

    assert is_sonoff_dongle({"brand": "sonoff", "model": "zbdongle-e"})
    assert is_sonoff_dongle({"brand": "sonoff", "model": "dongle-pmg24"})
    assert not is_sonoff_dongle({"brand": "nabucasa", "model": "skyconnect"})
    assert not is_sonoff_dongle({"brand": "sonoff", "model": "other-model"})


def test_manifest_changed_ignores_refreshed_at_only():
    manifest = build()
    same = dict(manifest, refreshedAt="2099-01-01T00:00:00Z")
    assert not manifest_changed(manifest, same)


def test_manifest_changed_detects_real_changes():
    manifest = build()
    assert manifest_changed(None, manifest)
    trimmed = dict(manifest, count=manifest["count"] - 1)
    assert manifest_changed(manifest, trimmed)


def shared_release(provider, tag="v1"):
    return {
        "tag_name": tag,
        "published_at": "2026-08-01T12:00:00Z",
        "prerelease": False,
        "assets": [{
            "name": "shared.gbl",
            "browser_download_url": provider.upstream_url(tag, "shared.gbl"),
            "size": 123,
        }],
    }


def shared_provider(source_id):
    return GitHubReleaseProvider(
        source_id=source_id,
        owner=source_id,
        repo="firmware",
        parse=lambda name: {
            "brand": "sonoff", "model": "dongle", "type": "bootloader",
            "version": "1.0.0",
        },
        keep=keep_all,
    )


def build_sources(sources, previous=None, recorder=None):
    return build_manifest(
        sources, previous, recorder or Recorder(),
        host_repo="SONOFF-Technologies/sonoff-dongle-series-open-source-firmware",
        refreshed_at="2026-08-14T07:07:00Z",
    )


def test_providers_with_shared_filename_preserve_json_shape_and_order():
    from firmware_list.files import MirrorFile

    first = shared_provider("vendor-a")
    second = shared_provider("vendor-b")
    recorder = Recorder()
    result = build_sources(
        [(first, [shared_release(first, "a1")]),
         (second, [shared_release(second, "b1")])],
        recorder=recorder,
    )
    manifest = result.manifest
    assert set(manifest) == {
        "owner", "repo", "releases", "refreshedAt", "count", "firmwares",
    }
    assert (manifest["owner"], manifest["repo"]) == ("vendor-a", "firmware")
    assert manifest["count"] == 2
    assert [release["tag"] for release in manifest["releases"]] == ["a1", "b1"]
    assert [fw["releaseTag"] for fw in manifest["firmwares"]] == ["a1", "b1"]
    for provider, tag, firmware in zip(
        (first, second), ("a1", "b1"), manifest["firmwares"],
    ):
        assert set(firmware) == {
            "brand", "model", "type", "version", "filename", "url",
            "size", "releaseTag", "prerelease", "sha256",
        }
        assert firmware["url"] == (
            "https://raw.githubusercontent.com/SONOFF-Technologies/sonoff-dongle-series-open-source-firmware/"
            f"main/firmwares/{provider.source_id}/shared.gbl"
        )
    assert result.mirror_files == [
        MirrorFile(Path("vendor-a/shared.gbl"),
                   first.upstream_url("a1", "shared.gbl"),
                   f"hash-of-{first.upstream_url('a1', 'shared.gbl')}"),
        MirrorFile(Path("vendor-b/shared.gbl"),
                   second.upstream_url("b1", "shared.gbl"),
                   f"hash-of-{second.upstream_url('b1', 'shared.gbl')}"),
    ]
    assert recorder.fetched == [
        (mirror.upstream_url, mirror.relative_path)
        for mirror in result.mirror_files
    ]


def test_rejects_duplicate_source_id_before_hashing():
    first = shared_provider("vendor-a")
    second = replace(first, owner="other-owner", repo="other-firmware")
    recorder = Recorder()

    with pytest.raises(ValueError, match="duplicate source_id.*vendor-a"):
        build_sources([
            (first, [shared_release(first)]),
            (second, [shared_release(second)]),
        ], recorder=recorder)

    assert recorder.fetched == []


def test_rejects_same_mirror_path_across_stable_and_prerelease_before_hashing():
    provider = shared_provider("vendor-a")
    stable = shared_release(provider, "stable")
    prerelease = shared_release(provider, "preview")
    prerelease["prerelease"] = True
    prerelease["assets"][0]["size"] = 456
    recorder = Recorder()

    with pytest.raises(ValueError, match=r"vendor-a.*shared\.gbl"):
        build_sources([(provider, [stable, prerelease])], recorder=recorder)

    assert recorder.fetched == []


def test_refuses_empty_provider_input_before_hashing():
    recorder = Recorder()

    with pytest.raises(ValueError, match="no firmware providers selected"):
        build_sources([], recorder=recorder)

    assert recorder.fetched == []


@pytest.mark.parametrize("failure", ["parse", "keep", "no-releases"])
def test_refuses_if_second_provider_has_no_firmware(failure):
    first = shared_provider("vendor-a")
    second = shared_provider("vendor-b")
    recorder = Recorder()
    release = shared_release(second)
    if failure == "parse":
        second = replace(second, parse=NERIVEC.parse)
    elif failure == "keep":
        second = replace(second, keep=lambda firmware: False)
    with pytest.raises(ValueError, match="vendor-b"):
        build_sources([
            (first, [shared_release(first)]),
            (second, [] if failure == "no-releases" else [release]),
        ], recorder=recorder)

    assert recorder.fetched == []


def test_reuses_legacy_flat_nerivec_checksum_during_namespace_migration():
    previous = stale_previous()
    for firmware in previous["firmwares"]:
        firmware["url"] = firmware["url"].replace(
            "/firmwares/nerivec/", "/firmwares/",
        )
    recorder = Recorder()
    manifest = build(previous=previous, recorder=recorder)
    assert recorder.fetched == []
    assert all("/firmwares/nerivec/" in fw["url"] for fw in manifest["firmwares"])
    assert [fw["sha256"] for fw in manifest["firmwares"]] == [
        fw["sha256"] for fw in previous["firmwares"]
    ]


def test_hash_reuse_is_scoped_to_provider_namespace():
    first = shared_provider("vendor-a")
    # Even providers sharing the upstream URL must use their own prior
    # public namespace, rather than mixing records from both sources.
    second = replace(first, source_id="vendor-b")
    sources = [(first, [shared_release(first)]), (second, [shared_release(second)])]
    previous = build_sources(sources).manifest
    previous["firmwares"][0]["sha256"] = "first-sha"
    previous["firmwares"][1]["sha256"] = "second-sha"
    recorder = Recorder()
    result = build_sources(sources, previous=previous, recorder=recorder)
    assert recorder.fetched == []
    assert [fw["sha256"] for fw in result.manifest["firmwares"]] == [
        "first-sha", "second-sha",
    ]


def test_flat_previous_records_are_not_reused_for_other_providers():
    provider = shared_provider("vendor-a")
    sources = [(provider, [shared_release(provider)])]
    previous = build_sources(sources).manifest
    previous["firmwares"][0]["url"] = previous["firmwares"][0]["url"].replace(
        "/firmwares/vendor-a/", "/firmwares/",
    )
    recorder = Recorder()
    build_sources(sources, previous=previous, recorder=recorder)
    assert recorder.fetched == [
        (provider.upstream_url("v1", "shared.gbl"), Path("vendor-a/shared.gbl")),
    ]
