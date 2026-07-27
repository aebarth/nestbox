"""Google Drive connector.

Auth: per-org OAuth client (Settings > google_client_id / google_client_secret),
user refresh token captured by the redirect flow in routers/cloud.py and stored
encrypted on the source row. Scope: https://www.googleapis.com/auth/drive
(read + move, needed for the wizard's filing step).
"""
import io
from datetime import datetime
from typing import IO, Iterator

from models import Source

from .base import RemoteFile

FOLDER_MIME = "application/vnd.google-apps.folder"


def _q(value: str) -> str:
    """Escape a string literal for a Drive API query."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GDriveConnector:
    def __init__(self, source: Source):
        self.source = source
        self.folder_id = source.config.get("folder_id", "root")
        self._svc = None

    # -- auth ---------------------------------------------------------------

    def _service(self):
        if self._svc is not None:
            return self._svc
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        from services.crypto import decrypt_json
        from services.settings import get_setting, get_secret_setting

        creds_data = decrypt_json(self.source.credentials_enc)
        if not creds_data or "refresh_token" not in creds_data:
            raise ValueError(
                f"Source '{self.source.name}' is not connected to Google yet. "
                "Use Admin > Sources > Connect Google."
            )
        creds = Credentials(
            token=None,
            refresh_token=creds_data["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=get_setting("google_client_id"),
            client_secret=get_secret_setting("google_client_secret_enc"),
        )
        self._svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._svc

    def test(self) -> None:
        svc = self._service()
        svc.files().get(fileId=self.folder_id, fields="id,name", supportsAllDrives=True).execute()

    # -- listing ------------------------------------------------------------

    def list_files(self) -> Iterator[RemoteFile]:
        svc = self._service()
        stack = [(self.folder_id, "")]  # (folder id, rel path prefix)
        while stack:
            folder_id, prefix = stack.pop()
            page_token = None
            while True:
                resp = (
                    svc.files()
                    .list(
                        q=f"'{_q(folder_id)}' in parents and trashed=false",
                        fields="nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime)",
                        pageSize=1000,
                        pageToken=page_token,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute()
                )
                for f in resp.get("files", []):
                    rel = f"{prefix}{f['name']}"
                    if f["mimeType"] == FOLDER_MIME:
                        stack.append((f["id"], f"{rel}/"))
                    elif f["name"].lower().endswith(".pdf") or f["mimeType"] == "application/pdf":
                        yield RemoteFile(
                            external_id=f["id"],
                            path=rel,
                            name=f["name"],
                            size=int(f.get("size", 0)),
                            mtime=_parse_ts(f.get("modifiedTime")),
                            checksum=("md5", f["md5Checksum"]) if f.get("md5Checksum") else None,
                        )
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

    # -- content / move -----------------------------------------------------

    def open_stream(self, f: RemoteFile) -> IO[bytes]:
        from googleapiclient.http import MediaIoBaseDownload

        svc = self._service()
        request = svc.files().get_media(fileId=f.external_id, supportsAllDrives=True)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return buf

    def _ensure_folder(self, rel_dir: str) -> str:
        """Return the folder id for rel_dir under the source root, creating as needed."""
        svc = self._service()
        parent = self.folder_id
        for part in [p for p in rel_dir.split("/") if p]:
            resp = (
                svc.files()
                .list(
                    q=(
                        f"'{_q(parent)}' in parents and name = '{_q(part)}' "
                        f"and mimeType = '{FOLDER_MIME}' and trashed=false"
                    ),
                    fields="files(id)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            found = resp.get("files", [])
            if found:
                parent = found[0]["id"]
            else:
                created = (
                    svc.files()
                    .create(
                        body={"name": part, "mimeType": FOLDER_MIME, "parents": [parent]},
                        fields="id",
                        supportsAllDrives=True,
                    )
                    .execute()
                )
                parent = created["id"]
        return parent

    def upload(self, stream: IO[bytes], new_rel_path: str) -> RemoteFile:
        """Create a new PDF at new_rel_path (wizard filing from the inbox)."""
        from googleapiclient.http import MediaIoBaseUpload

        svc = self._service()
        new_dir, _, new_name = new_rel_path.rpartition("/")
        dest_folder = self._ensure_folder(new_dir)
        media = MediaIoBaseUpload(stream, mimetype="application/pdf", resumable=True)
        created = (
            svc.files()
            .create(
                body={"name": new_name, "parents": [dest_folder]},
                media_body=media,
                fields="id,name,size,md5Checksum,modifiedTime",
                supportsAllDrives=True,
            )
            .execute()
        )
        return RemoteFile(
            external_id=created["id"],
            path=new_rel_path,
            name=created["name"],
            size=int(created.get("size", 0)),
            mtime=_parse_ts(created.get("modifiedTime")),
            checksum=("md5", created["md5Checksum"]) if created.get("md5Checksum") else None,
        )

    def move(self, f: RemoteFile, new_rel_path: str) -> RemoteFile:
        svc = self._service()
        new_dir, _, new_name = new_rel_path.rpartition("/")
        dest_folder = self._ensure_folder(new_dir)
        current = (
            svc.files()
            .get(fileId=f.external_id, fields="parents", supportsAllDrives=True)
            .execute()
        )
        updated = (
            svc.files()
            .update(
                fileId=f.external_id,
                addParents=dest_folder,
                removeParents=",".join(current.get("parents", [])),
                body={"name": new_name},
                fields="id,name,size,md5Checksum,modifiedTime",
                supportsAllDrives=True,
            )
            .execute()
        )
        return RemoteFile(
            external_id=updated["id"],
            path=new_rel_path,
            name=updated["name"],
            size=int(updated.get("size", 0)),
            mtime=_parse_ts(updated.get("modifiedTime")),
            checksum=("md5", updated["md5Checksum"]) if updated.get("md5Checksum") else None,
        )


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
