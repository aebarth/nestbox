from dataclasses import dataclass
from datetime import datetime
from typing import IO, Iterator, Protocol


@dataclass
class RemoteFile:
    external_id: str | None  # stable provider file ID; None for local files
    path: str                # POSIX path relative to the source root
    name: str
    size: int
    mtime: datetime | None
    checksum: tuple[str, str] | None = None  # (algo, value) when the provider supplies one


class Connector(Protocol):
    def test(self) -> None:
        """Raise a descriptive exception if the source is misconfigured/unreachable."""
        ...

    def list_files(self) -> Iterator[RemoteFile]:
        """Yield every PDF under the source root, recursively."""
        ...

    def open_stream(self, f: RemoteFile) -> IO[bytes]:
        """Open the file's content for hashing or preview."""
        ...

    def move(self, f: RemoteFile, new_rel_path: str) -> RemoteFile:
        """Move/rename within the source; returns the updated file."""
        ...
