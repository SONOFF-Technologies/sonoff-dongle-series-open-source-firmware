import pytest

from firmware_list.providers.github import GitHubReleaseProvider
from firmware_list.providers.nerivec import NERIVEC, is_sonoff_dongle


def parse_stub(filename):
    return {"filename": filename}


def keep_all(_firmware):
    return True


def test_github_release_provider_builds_urls_and_mirror_path():
    provider = GitHubReleaseProvider(
        source_id="vendor-a",
        owner="example",
        repo="firmwares",
        parse=parse_stub,
        keep=keep_all,
    )

    assert provider.api_url == (
        "https://api.github.com/repos/example/firmwares/releases?per_page=30"
    )
    assert provider.upstream_url("v1", "radio.gbl") == (
        "https://github.com/example/firmwares/releases/download/v1/radio.gbl"
    )
    assert provider.mirror_path("radio.gbl").as_posix() == (
        "vendor-a/radio.gbl"
    )


@pytest.mark.parametrize("source_id", ["../escape", "nested/path", "UPPER", ""])
def test_github_release_provider_rejects_invalid_source_ids(source_id):
    with pytest.raises(ValueError):
        GitHubReleaseProvider(
            source_id=source_id,
            owner="example",
            repo="firmwares",
            parse=parse_stub,
            keep=keep_all,
        )


@pytest.mark.parametrize(
    "filename",
    [
        "nested/radio.gbl",
        "nested\\radio.gbl",
        "C:radio.gbl",
        "C:\\radio.gbl",
        "..",
    ],
)
def test_github_release_provider_rejects_non_basename_mirror_files(filename):
    provider = GitHubReleaseProvider(
        source_id="vendor-a",
        owner="example",
        repo="firmwares",
        parse=parse_stub,
        keep=keep_all,
    )

    with pytest.raises(ValueError):
        provider.mirror_path(filename)


def test_nerivec_provider_uses_existing_parser_and_sonoff_dongle_filter():
    assert NERIVEC.source_id == "nerivec"
    assert NERIVEC.owner == "Nerivec"
    assert NERIVEC.repo == "silabs-firmware-builder"
    assert NERIVEC.parse(
        "sonoff_zbdonglee_zigbee_ncp_8.2.2.0_115200_sw_flow.gbl"
    ) == {
        "brand": "sonoff",
        "model": "zbdonglee",
        "type": "zigbee_ncp",
        "version": "8.2.2.0",
        "baudRate": 115200,
        "flowControl": "sw_flow",
    }
    assert is_sonoff_dongle({"brand": "sonoff", "model": "zbdongle-e"})
    assert is_sonoff_dongle({"brand": "sonoff", "model": "dongle-pmg24"})
    assert not is_sonoff_dongle({"brand": "nabucasa", "model": "skyconnect"})
    assert not is_sonoff_dongle({"brand": "sonoff", "model": "other-model"})
