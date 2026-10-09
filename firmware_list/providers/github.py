"""Interfaces and shared URL/path behavior for GitHub release sources."""

import ntpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

Parser = Callable[[str], Dict[str, object]]
KeepFilter = Callable[[Dict[str, Any]], bool]


@dataclass(frozen=True)
class GitHubReleaseProvider:
    source_id: str
    owner: str
    repo: str
    parse: Parser
    keep: KeepFilter

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.source_id) is None:
            raise ValueError(f"invalid source_id: {self.source_id!r}")

    @property
    def api_url(self) -> str:
        return (
            f"https://api.github.com/repos/{self.owner}/{self.repo}"
            "/releases?per_page=30"
        )

    def upstream_url(self, release_tag: str, filename: str) -> str:
        return (
            f"https://github.com/{self.owner}/{self.repo}/releases/download/"
            f"{release_tag}/{filename}"
        )

    def mirror_path(self, filename: str) -> Path:
        if (
            not filename
            or filename in (".", "..")
            or ntpath.splitdrive(filename)[0]
            or "/" in filename
            or "\\" in filename
        ):
            raise ValueError(f"filename must be a basename: {filename!r}")
        return Path(self.source_id, filename)
