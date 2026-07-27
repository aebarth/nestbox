import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import IO, Iterator

from models import Source

from .base import RemoteFile


class LocalConnector:
    """Folder under the /sources (or /inbox) bind mount."""

    def __init__(self, source: Source):
        root = source.config.get("root", "")
        self.root = Path(root)

    def test(self) -> None:
        allowed = tuple(
            p for p in os.environ.get("NESTBOX_SOURCE_ROOTS", "/sources,/inbox").split(",") if p
        )
        if not str(self.root).startswith(allowed):
            raise ValueError(
                f"Local source root must live under /sources or /inbox, got: {self.root}"
            )
        if not self.root.is_dir():
            raise FileNotFoundError(
                f"Folder not found inside the container: {self.root}. "
                "Check NESTBOX_SOURCES_PATH in .env and the source's folder setting."
            )

    def _abs(self, rel_path: str) -> Path:
        root = self.root.resolve()
        p = (self.root / rel_path.lstrip("/")).resolve()
        # is_relative_to (not startswith): "/sources/qa2" must not pass for a
        # root of "/sources/qa"
        if not p.is_relative_to(root):
            raise ValueError(f"Path escapes the source root: {rel_path}")
        return p

    def list_files(self) -> Iterator[RemoteFile]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if not fn.lower().endswith(".pdf") or fn.startswith("."):
                    continue
                full = Path(dirpath) / fn
                try:
                    st = full.stat()
                except OSError:
                    continue
                rel = str(PurePosixPath(full.relative_to(self.root)))
                yield RemoteFile(
                    external_id=None,
                    path=rel,
                    name=fn,
                    size=st.st_size,
                    mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
                )

    def open_stream(self, f: RemoteFile) -> IO[bytes]:
        return open(self._abs(f.path), "rb")

    def move(self, f: RemoteFile, new_rel_path: str) -> RemoteFile:
        src = self._abs(f.path)
        dst = self._abs(new_rel_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise FileExistsError(f"Destination already exists: {new_rel_path}")
        os.rename(src, dst)
        st = dst.stat()
        return RemoteFile(
            external_id=None,
            path=new_rel_path,
            name=dst.name,
            size=st.st_size,
            mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
        )

    def move_to(self, f: RemoteFile, other: "LocalConnector", new_rel_path: str) -> RemoteFile:
        """Cross-source move (inbox -> destination source). Local-to-local only."""
        src = self._abs(f.path)
        dst = other._abs(new_rel_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise FileExistsError(f"Destination already exists: {new_rel_path}")
        try:
            os.rename(src, dst)
        except OSError:
            # /inbox and /sources are usually different mounts; fall back to copy+delete
            import shutil

            shutil.copy2(src, dst)
            os.unlink(src)
        st = dst.stat()
        return RemoteFile(
            external_id=None,
            path=new_rel_path,
            name=dst.name,
            size=st.st_size,
            mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
        )
