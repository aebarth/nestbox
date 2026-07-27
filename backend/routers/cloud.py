"""Cloud connector plumbing: Google OAuth redirect flow and Graph drive discovery."""
import os

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Source, User
from security import SECRET_KEY, require_admin
from services.crypto import encrypt_json
from services.settings import get_secret_setting, get_setting

router = APIRouter(prefix="/api/connectors", tags=["connectors"])

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _redirect_uri() -> str:
    base = os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/")
    return f"{base}/api/connectors/google/callback"


def _flow():
    from google_auth_oauthlib.flow import Flow

    client_id = get_setting("google_client_id")
    client_secret = get_secret_setting("google_client_secret_enc")
    if not (client_id and client_secret):
        raise HTTPException(
            status_code=422,
            detail="Google is not configured. Set the client ID and secret in "
                   "Admin > Settings first (see docs/google-drive-setup.md).",
        )
    return Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GOOGLE_SCOPES,
        redirect_uri=_redirect_uri(),
    )


@router.get("/google/authorize", dependencies=[Depends(require_admin)])
def google_authorize(source_id: int, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None or source.kind != "gdrive":
        raise HTTPException(status_code=404, detail="Google Drive source not found")
    state = jwt.encode({"source_id": source_id, "purpose": "gauth"}, SECRET_KEY, algorithm="HS256")
    url, _ = _flow().authorization_url(
        access_type="offline", prompt="consent", state=state, include_granted_scopes="true"
    )
    return {"url": url}


@router.get("/google/callback")
def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    # No cookie check here: Google redirects the browser straight to this URL.
    # The signed state token proves the flow started from an admin session.
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=["HS256"])
        assert payload.get("purpose") == "gauth"
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state")
    source = db.get(Source, payload["source_id"])
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    flow = _flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Google did not return a refresh token. Remove the app's access at "
                   "https://myaccount.google.com/permissions and reconnect.",
        )
    from database import set_actor

    set_actor(db, "google-oauth")
    source.credentials_enc = encrypt_json({"refresh_token": creds.refresh_token})
    db.commit()
    base = os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/")
    return RedirectResponse(f"{base}/admin?connected=google")


@router.get("/graph/drives", dependencies=[Depends(require_admin)])
def graph_drives(q: str = ""):
    """Help admins find a drive_id: pass a user email for their OneDrive, or a
    keyword to search SharePoint sites."""
    import requests as rq

    from connectors.graph import GRAPH, acquire_app_token

    token = acquire_app_token()
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    if "@" in q:
        resp = rq.get(f"{GRAPH}/users/{q}/drive", headers=headers, timeout=30)
        if resp.ok:
            d = resp.json()
            results.append({"drive_id": d["id"], "name": f"OneDrive — {q}", "kind": "onedrive"})
    else:
        resp = rq.get(f"{GRAPH}/sites?search={q or '*'}", headers=headers, timeout=30)
        resp.raise_for_status()
        for site in resp.json().get("value", [])[:10]:
            drives = rq.get(f"{GRAPH}/sites/{site['id']}/drives", headers=headers, timeout=30)
            if drives.ok:
                for d in drives.json().get("value", []):
                    results.append({
                        "drive_id": d["id"],
                        "name": f"{site.get('displayName', site['id'])} — {d['name']}",
                        "kind": "sharepoint",
                    })
    return results
