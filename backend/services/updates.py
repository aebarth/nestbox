"""In-app update/upgrade service (#6, Phase 6).

Tier 1 (default, safe, guided): the admin checks for updates from the
GitHub Releases API, triggers an auto-backup, and receives the exact
docker compose commands to run on the host. After restart, migrations
run automatically and the new version is visible in Admin.

Tier 2 (opt-in one-click via Docker socket) is a follow-up.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("nestbox.updates")

# The GitHub repo that hosts releases. Overridable for forks/testing.
GITHUB_REPO = os.environ.get("NESTBOX_GITHUB_REPO", "aebarth/nestbox")
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Cache TTL for the GitHub API response (seconds). Avoids hammering the
# API on every Admin page load while staying fresh enough for real use.
_CACHE_TTL = 300  # 5 minutes


class UpdateError(Exception):
    """User-facing failure of an update operation."""


# ---------------------------------------------------------------------------
# Version tracking
# ---------------------------------------------------------------------------

def installed_version() -> str:
    """The app version from NESTBOX_VERSION env, defaulting to '1.0.0'."""
    return os.environ.get("NESTBOX_VERSION", "1.0.0")


def schema_version(db: Session) -> int:
    """Current database schema version from the schema_version table."""
    row = db.execute(text("SELECT max(version) FROM schema_version")).scalar()
    return row or 0


def persist_installed_version(db: Session) -> None:
    """Record the installed version in the settings table (idempotent).

    Called at startup so Admin can display it even if the env var is
    unavailable to the frontend. Uses upsert semantics: only writes if
    the value changed, avoiding unnecessary audit noise on every boot.
    """
    from models import Setting

    ver = installed_version()
    existing = db.execute(
        text("SELECT value FROM settings WHERE key = 'installed_version'")
    ).scalar_one_or_none()
    if existing != ver:
        db.merge(Setting(key="installed_version", value=ver))
        db.commit()


# ---------------------------------------------------------------------------
# Check for updates (GitHub Releases API)
# ---------------------------------------------------------------------------

@dataclass
class ReleaseInfo:
    """Parsed GitHub release data."""
    tag_name: str
    name: str
    body: str  # markdown release notes
    published_at: str  # ISO timestamp
    html_url: str


@dataclass
class _CacheEntry:
    release: ReleaseInfo
    fetched_at: float = field(default_factory=time.time)


_cache: _CacheEntry | None = None


def check_for_update(db: Session, force: bool = False) -> dict:
    """Check GitHub for the latest release and compare to installed version.

    Returns a dict with:
      - installed_version: str
      - latest_version: str (tag without leading 'v')
      - update_available: bool
      - release_notes: str | None
      - published_at: str | None
      - html_url: str | None
      - checked_at: str (ISO timestamp of when this check was performed)

    Raises UpdateError on network failure (the frontend shows a retry).
    """
    global _cache

    now = time.time()
    if not force and _cache is not None and (now - _cache.fetched_at) < _CACHE_TTL:
        release = _cache.release
    else:
        try:
            resp = httpx.get(GITHUB_API_URL, timeout=10.0, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"nestbox-{installed_version()}",
            })
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # No releases yet — the repo exists but hasn't tagged one.
                release = ReleaseInfo(
                    tag_name="none", name="", body="",
                    published_at="", html_url="",
                )
            else:
                raise UpdateError(
                    f"GitHub API returned {exc.response.status_code} while checking "
                    f"for updates (repo: {GITHUB_REPO})"
                ) from exc
        except httpx.RequestError as exc:
            raise UpdateError(
                f"Could not reach the GitHub API to check for updates: {exc!r}"
            ) from exc
        else:
            release = ReleaseInfo(
                tag_name=data.get("tag_name", ""),
                name=data.get("name", ""),
                body=data.get("body") or "",
                published_at=data.get("published_at", ""),
                html_url=data.get("html_url", ""),
            )
        _cache = _CacheEntry(release=release, fetched_at=now)

    # Compare versions (strip leading 'v' from tags).
    installed = installed_version()
    latest = release.tag_name.lstrip("v") if release.tag_name != "none" else ""
    update_available = bool(latest) and _version_tuple(latest) > _version_tuple(installed)

    return {
        "installed_version": installed,
        "latest_version": latest,
        "update_available": update_available,
        "release_notes": release.body if update_available else None,
        "published_at": release.published_at if update_available else None,
        "html_url": release.html_url if update_available else None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse '1.2.3' → (1, 2, 3) for comparison. Non-numeric parts → 0."""
    parts = []
    for piece in v.split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def invalidate_cache() -> None:
    """Drop the cached release info (used by tests)."""
    global _cache
    _cache = None


# ---------------------------------------------------------------------------
# Prepare update (auto-backup + compose commands)
# ---------------------------------------------------------------------------

def prepare_update(db: Session, actor_username: str, actor_user_id: int | None = None) -> dict:
    """Create an auto-backup and return the docker compose update commands.

    The returned dict includes:
      - backup_id: int (the new backup's row id)
      - backup_filename: str
      - commands: list[str] (the exact shell commands to run on the host)
      - pinned_version: str (the latest version to pin, if available)

    Raises UpdateError if the backup fails.
    """
    from services import lifecycle
    from services.lifecycle import LifecycleError

    try:
        backup = lifecycle.create_backup(
            db, kind="pre_update", actor_username=actor_username,
            actor_user_id=actor_user_id, notes="Auto-backup before update",
        )
    except LifecycleError as exc:
        raise UpdateError(f"Pre-update backup failed: {exc}") from exc

    # Build the compose commands. The admin runs these on the Docker host.
    latest = _latest_tag()
    if latest:
        commands = [
            f"# Pin the target version in your .env (optional — pulls latest by default):",
            f"echo 'NESTBOX_VERSION={latest}' >> .env",
            "",
            "# Pull new images and recreate containers:",
            "docker compose pull && docker compose up -d",
        ]
    else:
        commands = [
            "# Pull the latest images and recreate containers:",
            "docker compose pull && docker compose up -d",
        ]

    return {
        "backup_id": backup.id,
        "backup_filename": backup.filename,
        "commands": commands,
        "pinned_version": latest or None,
        "message": (
            f"Backup created ({backup.filename}). Run the commands above on your Docker host. "
            "After restart, migrations run automatically and the new version will appear here."
        ),
    }


def _latest_tag() -> str:
    """Return the latest release tag (without 'v') from cache, or '' if unknown."""
    if _cache and _cache.release.tag_name != "none":
        return _cache.release.tag_name.lstrip("v")
    return ""
