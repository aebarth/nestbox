"""OneDrive / SharePoint connector via Microsoft Graph.

Auth: app-only client-credentials flow (single-tenant Entra app with
application permissions Files.ReadWrite.All + Sites.ReadWrite.All, admin
consent granted once). Tenant/client/secret live in Settings, so scheduled
scans run unattended with no user token to expire.

OneDrive and SharePoint document libraries are both Graph "drives"; a source
is configured with a drive_id (picked in Admin > Sources) and an optional
folder path inside it.
"""
import io
import time
from datetime import datetime
from typing import IO, Iterator
from urllib.parse import quote

import requests

from models import Source

from .base import RemoteFile

GRAPH = "https://graph.microsoft.com/v1.0"


def acquire_app_token() -> str:
    import msal

    from services.settings import get_setting, get_secret_setting

    tenant = get_setting("graph_tenant_id")
    client_id = get_setting("graph_client_id")
    secret = get_secret_setting("graph_client_secret_enc")
    if not (tenant and client_id and secret):
        raise ValueError(
            "Microsoft 365 is not configured. Set tenant ID, client ID and client "
            "secret in Admin > Settings (see docs/microsoft-365-setup.md)."
        )
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=secret,
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Graph auth failed: {result.get('error_description', result)}")
    return result["access_token"]


class GraphConnector:
    def __init__(self, source: Source):
        self.source = source
        self.drive_id = source.config.get("drive_id", "")
        self.folder_path = source.config.get("folder_path", "").strip("/")
        self._token: str | None = None

    def _get(self, url: str, **kwargs) -> requests.Response:
        if self._token is None:
            self._token = acquire_app_token()
        for attempt in range(5):
            resp = requests.request(
                kwargs.pop("method", "GET"),
                url,
                headers={"Authorization": f"Bearer {self._token}", **kwargs.pop("headers", {})},
                timeout=60,
                **kwargs,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(int(resp.headers.get("Retry-After", 2 ** attempt)))
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    def _root_url(self) -> str:
        if self.folder_path:
            return f"{GRAPH}/drives/{self.drive_id}/root:/{quote(self.folder_path)}:"
        return f"{GRAPH}/drives/{self.drive_id}/root"

    def test(self) -> None:
        if not self.drive_id:
            raise ValueError(f"Source '{self.source.name}' has no drive selected")
        self._get(self._root_url())

    def list_files(self) -> Iterator[RemoteFile]:
        # Full enumeration by folder recursion. (Delta queries would make
        # re-scans cheaper; enumeration keeps missing-file detection simple
        # and correct — revisit if scans of huge libraries become slow.)
        root = self._get(self._root_url()).json()
        stack = [(root["id"], "")]
        while stack:
            item_id, prefix = stack.pop()
            url = f"{GRAPH}/drives/{self.drive_id}/items/{item_id}/children?$top=200"
            while url:
                data = self._get(url).json()
                for item in data.get("value", []):
                    rel = f"{prefix}{item['name']}"
                    if "folder" in item:
                        stack.append((item["id"], f"{rel}/"))
                    elif item["name"].lower().endswith(".pdf"):
                        hashes = item.get("file", {}).get("hashes", {})
                        qxh = hashes.get("quickXorHash")
                        yield RemoteFile(
                            external_id=item["id"],
                            path=rel,
                            name=item["name"],
                            size=int(item.get("size", 0)),
                            mtime=_parse_ts(item.get("lastModifiedDateTime")),
                            checksum=("quickxor", qxh) if qxh else None,
                        )
                url = data.get("@odata.nextLink")

    def open_stream(self, f: RemoteFile) -> IO[bytes]:
        resp = self._get(f"{GRAPH}/drives/{self.drive_id}/items/{f.external_id}/content")
        return io.BytesIO(resp.content)

    def _ensure_folder(self, rel_dir: str) -> str:
        """Return the item id of rel_dir under the source root, creating as needed."""
        root = self._get(self._root_url()).json()
        parent_id = root["id"]
        for part in [p for p in rel_dir.split("/") if p]:
            odata_name = part.replace("'", "''")  # OData string-literal escaping
            children = self._get(
                f"{GRAPH}/drives/{self.drive_id}/items/{parent_id}/children"
                f"?$filter=name eq '{quote(odata_name)}'"
            ).json()
            match = next((c for c in children.get("value", []) if "folder" in c), None)
            if match:
                parent_id = match["id"]
            else:
                created = self._get(
                    f"{GRAPH}/drives/{self.drive_id}/items/{parent_id}/children",
                    method="POST",
                    json={"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
                    headers={"Content-Type": "application/json"},
                ).json()
                parent_id = created["id"]
        return parent_id

    def upload(self, stream: IO[bytes], new_rel_path: str) -> RemoteFile:
        """Create a new PDF at new_rel_path (wizard filing from the inbox).

        Simple upload is capped at ~4 MB by Graph, so use an upload session.
        """
        new_dir, _, new_name = new_rel_path.rpartition("/")
        parent_id = self._ensure_folder(new_dir)
        session = self._get(
            f"{GRAPH}/drives/{self.drive_id}/items/{parent_id}:/{quote(new_name)}:/createUploadSession",
            method="POST",
            json={"item": {"@microsoft.graph.conflictBehavior": "fail"}},
            headers={"Content-Type": "application/json"},
        ).json()
        upload_url = session["uploadUrl"]
        data = stream.read()
        chunk = 5 * 1024 * 1024
        item = None
        for start in range(0, len(data), chunk):
            end = min(start + chunk, len(data))
            resp = requests.put(
                upload_url,
                data=data[start:end],
                headers={
                    "Content-Length": str(end - start),
                    "Content-Range": f"bytes {start}-{end - 1}/{len(data)}",
                },
                timeout=300,
            )
            resp.raise_for_status()
            if resp.status_code in (200, 201):
                item = resp.json()
        if item is None:
            raise RuntimeError("Graph upload did not complete")
        hashes = item.get("file", {}).get("hashes", {})
        qxh = hashes.get("quickXorHash")
        return RemoteFile(
            external_id=item["id"],
            path=new_rel_path,
            name=item["name"],
            size=int(item.get("size", 0)),
            mtime=_parse_ts(item.get("lastModifiedDateTime")),
            checksum=("quickxor", qxh) if qxh else None,
        )

    def move(self, f: RemoteFile, new_rel_path: str) -> RemoteFile:
        new_dir, _, new_name = new_rel_path.rpartition("/")
        dest_id = self._ensure_folder(new_dir)
        updated = self._get(
            f"{GRAPH}/drives/{self.drive_id}/items/{f.external_id}",
            method="PATCH",
            json={"parentReference": {"id": dest_id}, "name": new_name},
            headers={"Content-Type": "application/json"},
        ).json()
        hashes = updated.get("file", {}).get("hashes", {})
        qxh = hashes.get("quickXorHash")
        return RemoteFile(
            external_id=updated["id"],
            path=new_rel_path,
            name=updated["name"],
            size=int(updated.get("size", 0)),
            mtime=_parse_ts(updated.get("lastModifiedDateTime")),
            checksum=("quickxor", qxh) if qxh else None,
        )


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
