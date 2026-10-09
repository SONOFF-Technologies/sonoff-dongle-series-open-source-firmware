"""Parse `.gbl` firmware filenames from silabs-firmware-builder releases.

Real-world filename grammar (upstream research doc, section 4.3 — the
README's six-segment convention is a simplification that does not hold):

    <device>_<fw_type>_<version>_<baudrate>_<variant>_flow.gbl  # zigbee / openthread
    <device>_<fw_type>_<version>.gbl                            # bootloader

`<variant>` is the flow-control mode: `sw`, `hw`, or `no` (rendered as
`sw_flow` / `hw_flow` / `no_flow`).

- `<fw_type>` is one of a closed set: zigbee_ncp, zigbee_router,
  openthread_rcp, bootloader.
- `<version>` starts with a digit and may itself contain underscores
  (e.g. OpenThread's `2.7.2.0_GitHub-fb0446f53`).
- bootloader files have no baudrate / flow-control suffix.
- `<device>` is `brand_model` for most boards but hyphen-joined for a few
  (e.g. `tubeszb-bm24`), so brand/model are split on the first `_` or `-`.
"""

import re
from typing import Dict

_FULL_RE = re.compile(
    r"^(?P<device>.+?)[_-]"
    r"(?P<fw_type>zigbee_ncp|zigbee_router|openthread_rcp)"
    r"_(?P<version>\d.+?)_(?P<baud>\d+)_(?P<variant>[a-z]+)_flow$"
)
_BOOTLOADER_RE = re.compile(
    r"^(?P<device>.+?)[_-](?P<fw_type>bootloader)_(?P<version>\d.+?)$"
)


def parse_gbl_filename(filename: str) -> Dict[str, object]:
    """Split a `.gbl` filename into its semantic fields.

    bootloader records have no `baudRate` / `flowControl` keys.
    Raises ValueError for non-`.gbl` names or unparseable stems.
    """
    if not filename.endswith(".gbl"):
        raise ValueError(f"not a .gbl file: {filename}")
    stem = filename[: -len(".gbl")]

    match = _FULL_RE.match(stem) or _BOOTLOADER_RE.match(stem)
    if match is None:
        raise ValueError(f"unrecognized firmware filename: {filename}")
    groups = match.groupdict()

    parts = re.split(r"[_-]", groups["device"], maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"cannot split device into brand/model: {filename}")
    record: Dict[str, object] = {
        "brand": parts[0],
        "model": parts[1],
        "type": groups["fw_type"],
        "version": groups["version"],
    }
    if "baud" in groups:
        record["baudRate"] = int(str(groups["baud"]))
        record["flowControl"] = f"{groups['variant']}_flow"
    return record
