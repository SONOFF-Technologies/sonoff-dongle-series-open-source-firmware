"""Nerivec/silabs-firmware-builder release source."""

from typing import Any, Dict

from firmware_list.parse import parse_gbl_filename
from firmware_list.providers.github import GitHubReleaseProvider


def is_sonoff_dongle(firmware: Dict[str, Any]) -> bool:
    """Keep SONOFF dongle series, including older zbdongle model names."""
    model = str(firmware.get("model", ""))
    return firmware.get("brand") == "sonoff" and (
        model.startswith("dongle") or model.startswith("zbdongle")
    )


NERIVEC = GitHubReleaseProvider(
    source_id="nerivec",
    owner="Nerivec",
    repo="silabs-firmware-builder",
    parse=parse_gbl_filename,
    keep=is_sonoff_dongle,
)
