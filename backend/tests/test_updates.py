"""In-app update/upgrade tests (#6, Phase 6).

Tier 1 (guided-default): version display, GitHub release check (mocked),
and the prepare flow (auto-backup + compose commands).
"""
import json
from unittest.mock import patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_release(tag: str = "v2.0.0", body: str = "Big new features", published: str = "2026-11-01T00:00:00Z"):
    """Build a mock httpx.Response for the GitHub Releases API."""
    return httpx.Response(
        200,
        json={
            "tag_name": tag,
            "name": f"Release {tag}",
            "body": body,
            "published_at": published,
            "html_url": f"https://github.com/aebarth/nestbox/releases/{tag}",
        },
    )


@pytest.fixture(autouse=True)
def _clear_update_cache():
    """Each test starts with a cold cache."""
    from services import updates

    updates.invalidate_cache()
    yield
    updates.invalidate_cache()


# ---------------------------------------------------------------------------
# Version endpoint
# ---------------------------------------------------------------------------

def test_version_returns_installed_and_schema(admin_client, db_session):
    r = admin_client.get("/api/admin/version")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "installed_version" in body
    assert "schema_version" in body
    # The default version (no NESTBOX_VERSION env set in test) is 1.0.0
    assert body["installed_version"] == "1.0.0"
    # Schema version should be a positive integer (migrations ran)
    assert isinstance(body["schema_version"], int) and body["schema_version"] >= 1


def test_version_reflects_env(monkeypatch, admin_client):
    monkeypatch.setenv("NESTBOX_VERSION", "9.9.9")
    r = admin_client.get("/api/admin/version")
    assert r.json()["installed_version"] == "9.9.9"


# ---------------------------------------------------------------------------
# Check for updates
# ---------------------------------------------------------------------------

def test_check_update_available(admin_client):
    """A newer release on GitHub → update_available=True + notes."""
    with patch("httpx.get", return_value=_mock_release(tag="v2.0.0")):
        r = admin_client.get("/api/admin/updates/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["installed_version"] == "1.0.0"
    assert body["latest_version"] == "2.0.0"
    assert body["update_available"] is True
    assert body["release_notes"] == "Big new features"
    assert body["published_at"] == "2026-11-01T00:00:00Z"
    assert "html_url" in body


def test_check_no_update_when_same_version(admin_client):
    """Installed version matches latest → update_available=False."""
    with patch("httpx.get", return_value=_mock_release(tag="v1.0.0")):
        r = admin_client.get("/api/admin/updates/check")
    body = r.json()
    assert body["update_available"] is False
    assert body["release_notes"] is None


def test_check_no_update_when_older(admin_client):
    """GitHub has an older version → no update."""
    with patch("httpx.get", return_value=_mock_release(tag="v0.9.0")):
        r = admin_client.get("/api/admin/updates/check")
    body = r.json()
    assert body["update_available"] is False


def test_check_cache_prevents_second_call(admin_client):
    """Second call within TTL uses the cache (httpx.get called once)."""
    mock_get = patch("httpx.get", return_value=_mock_release(tag="v3.0.0")).start()
    try:
        r1 = admin_client.get("/api/admin/updates/check")
        assert r1.json()["latest_version"] == "3.0.0"
        # Second call should hit the cache
        r2 = admin_client.get("/api/admin/updates/check")
        assert r2.json()["latest_version"] == "3.0.0"
        assert mock_get.call_count == 1, f"httpx.get called {mock_get.call_count} times (expected 1)"
    finally:
        patch.stopall()


def test_check_force_bypasses_cache(admin_client):
    """?force=true re-queries even within the TTL window."""
    mock_get = patch("httpx.get", return_value=_mock_release(tag="v3.0.0")).start()
    try:
        admin_client.get("/api/admin/updates/check")
        assert mock_get.call_count == 1
        # Force a fresh check
        r = admin_client.get("/api/admin/updates/check?force=true")
        assert r.status_code == 200
        assert mock_get.call_count == 2
    finally:
        patch.stopall()


def test_check_network_error_returns_502(admin_client):
    """GitHub API unreachable → 502 with a clear message."""
    with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        r = admin_client.get("/api/admin/updates/check")
    assert r.status_code == 502
    assert "github" in r.json()["detail"].lower() or "could not reach" in r.json()["detail"].lower()


def test_check_404_means_no_releases(admin_client):
    """Repo has no releases yet (404) → latest_version is empty, no crash."""
    with patch("httpx.get", return_value=httpx.Response(404, json={"message": "Not Found"})):
        r = admin_client.get("/api/admin/updates/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["update_available"] is False
    assert body["latest_version"] == ""


# ---------------------------------------------------------------------------
# Prepare update
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def backup_tmpdir(tmp_path, monkeypatch):
    """Backups land in a per-test dir (CI has no /backups mount)."""
    d = tmp_path / "backups"
    monkeypatch.setenv("NESTBOX_BACKUP_PATH", str(d))
    return d


def test_prepare_creates_backup_and_returns_commands(admin_client):
    """Prepare creates a pre-update backup + returns docker compose commands."""
    # Seed the cache so prepare knows the target version
    with patch("httpx.get", return_value=_mock_release(tag="v2.1.0")):
        admin_client.get("/api/admin/updates/check")

    r = admin_client.post("/api/admin/updates/prepare")
    assert r.status_code == 200, r.text
    body = r.json()

    # Backup was created
    assert body["backup_id"] is not None
    assert "nestbox-" in body["backup_filename"]
    assert ".dump" in body["backup_filename"]

    # Pinned version from the cached release check
    assert body["pinned_version"] == "2.1.0"

    # Commands include docker compose pull + up
    cmd_text = "\n".join(body["commands"])
    assert "docker compose pull" in cmd_text
    assert "docker compose up -d" in cmd_text
    assert "NESTBOX_VERSION=2.1.0" in cmd_text

    # Human-readable message
    assert "backup" in body["message"].lower()


def test_prepare_without_prior_check_still_works(admin_client):
    """Prepare works even without a prior /check (no pinned version)."""
    r = admin_client.post("/api/admin/updates/prepare")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["backup_id"] is not None
    # No cached release → no pin
    assert body["pinned_version"] is None
    cmd_text = "\n".join(body["commands"])
    assert "docker compose pull" in cmd_text


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_updates_require_admin(admin_client, default_org_id):
    """Non-admin users cannot access update endpoints."""
    from fastapi.testclient import TestClient

    from main import app

    r = admin_client.post("/api/admin/users", json={
        "username": "upd-bob", "password": "bobpass123",
        "full_name": "Bob", "role": "user",
        "org_id": default_org_id, "org_role": "member",
    })
    assert r.status_code == 200, r.text

    with TestClient(app) as bob:
        login = bob.post("/api/auth/login", json={"username": "upd-bob", "password": "bobpass123"})
        assert login.status_code == 200, login.text
        assert bob.get("/api/admin/version").status_code == 403
        assert bob.get("/api/admin/updates/check").status_code == 403
        assert bob.post("/api/admin/updates/prepare").status_code == 403


def test_updates_require_auth():
    """Unauthenticated requests get 401."""
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as anon:
        assert anon.get("/api/admin/version").status_code == 401
        assert anon.get("/api/admin/updates/check").status_code == 401
        assert anon.post("/api/admin/updates/prepare").status_code == 401
