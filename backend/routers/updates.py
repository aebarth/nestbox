"""In-app update/upgrade endpoints (#6, Phase 6).

Tier 1 (guided-default): the admin checks for updates, triggers an
auto-backup, and receives the exact docker compose commands to run on
the host. After restart, migrations run automatically.

All endpoints require install-admin auth (same guard as /api/admin/*).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import User
from security import require_admin
from services import updates
from services.updates import UpdateError

router = APIRouter(prefix="/api/admin", tags=["updates"], dependencies=[Depends(require_admin)])


@router.get("/version")
def get_version(db: Session = Depends(get_db)):
    """Return the installed app version and current schema version.

    The frontend displays this in Admin so the user can see at a glance
    whether they're on the latest release (paired with /updates/check).
    """
    return {
        "installed_version": updates.installed_version(),
        "schema_version": updates.schema_version(db),
    }


@router.get("/updates/check")
def check_for_updates(force: bool = False, db: Session = Depends(get_db)):
    """Check GitHub for the latest release.

    Returns installed vs latest version, whether an update is available,
    and (if so) the release notes + publish date. Results are cached for
    5 minutes; pass ?force=true to bypass the cache.

    Raises 502 on network failure (the frontend shows a retry button).
    """
    try:
        return updates.check_for_update(db, force=force)
    except UpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/updates/prepare")
def prepare_update(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Create a pre-update auto-backup and return the docker compose commands.

    The returned body includes:
      - backup_id / backup_filename: the new backup (visible in Admin → Backups)
      - commands: shell commands to copy-paste on the Docker host
      - pinned_version: the target version tag (or null if unknown)
      - message: human-readable summary

    After running the commands, the container restarts with the new image;
    migrations run automatically and /api/admin/version reflects the change.
    """
    try:
        return updates.prepare_update(db, actor_username=admin.username, actor_user_id=admin.id)
    except UpdateError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
