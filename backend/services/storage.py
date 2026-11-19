"""Per-organization storage locations (#15).

Install-level bind mounts are fixed at container start — /sources, /inbox,
/backups (docker-compose.yml). Each organization gets its own subtree under
those mounts (/sources/<slug>, /inbox/<slug>, /backups/<slug>); the effective
paths live in org_settings and fall back to the slug-based defaults when a
key is unset. Cloud Drive sources are exempt from the subtree rule — their
"location" is the drive itself.
"""
import os
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Organization, OrgSetting

# location -> (container mount root, org_settings key, host-path env var)
LOCATIONS = {
    "inbox": ("/inbox", "storage_inbox_root", "NESTBOX_INBOX_HOST_PATH"),
    "sources": ("/sources", "storage_sources_root", "NESTBOX_SOURCES_HOST_PATH"),
    "backups": ("/backups", "storage_backup_path", "NESTBOX_BACKUP_HOST_PATH"),
}

FILE_BASED_KINDS = ("local", "smb", "nfs")


def default_paths(slug: str) -> dict[str, str]:
    """Slug-based default storage paths for a new organization."""
    return {key: f"{root}/{slug}" for root, key, _ in LOCATIONS.values()}


def effective_storage(db: Session, org: Organization) -> dict[str, str]:
    """The org's effective storage paths (org_settings values or slug defaults).

    The session's RLS context must be scoped to this org for custom values to
    be visible; callers managing another org re-set the GUC first.
    """
    rows = db.execute(select(OrgSetting).where(OrgSetting.org_id == org.id)).scalars().all()
    values = {r.key: r.value for r in rows}
    defaults = default_paths(org.slug)
    out: dict[str, str] = {}
    for location, (_, key, _) in LOCATIONS.items():
        v = values.get(key)
        out[location] = v.rstrip("/") if isinstance(v, str) and v else defaults[key]
    return out


def org_backup_dir(db: Session, org: Organization) -> Path:
    """Directory where this org's backup zips are written (services/org_backup).

    A genuinely custom Admin → Storage path (storage_backup_path differing
    from the slug default) wins; otherwise the slug-based default under the
    install-level backup root — NESTBOX_BACKUP_PATH when set (dev / CI runs
    without the container mount), else the /backups mount. Mirrors
    services/lifecycle.py::backup_dir so both backup tiers share one root by
    default.

    Note: migration 007 and org creation persist the slug default itself as an
    org_settings row, so "a row exists" does NOT mean "custom" — only a value
    different from the default is treated as explicit. (If an admin sets the
    path back to exactly the slug default while NESTBOX_BACKUP_PATH points
    elsewhere, zips follow the env root; in compose both are /backups.) The
    session's RLS context must be scoped to this org for custom values to be
    visible.
    """
    defaults = default_paths(org.slug)
    row = db.execute(
        select(OrgSetting).where(
            OrgSetting.org_id == org.id, OrgSetting.key == "storage_backup_path"
        )
    ).scalars().first()
    if row is not None and isinstance(row.value, str) and row.value:
        value = row.value.rstrip("/")
        if value != defaults["backups"]:
            return Path(value)
    root = os.environ.get("NESTBOX_BACKUP_PATH", "").strip() or "/backups"
    return Path(root) / org.slug


def validate_location(location: str, path: str) -> str:
    """Validate a per-org location path; return it normalized (no trailing /).

    Raises ValueError with a user-facing message when invalid.
    """
    if location not in LOCATIONS:
        raise ValueError(f"Unknown storage location: {location}")
    root = LOCATIONS[location][0]
    normalized = str(path).rstrip("/")
    if not normalized.startswith("/"):
        raise ValueError(f"Path must be absolute, e.g. {root}/acme/records")
    if ".." in normalized.strip("/").split("/"):
        raise ValueError("Path must not contain '..' segments")
    if normalized != root and not normalized.startswith(root + "/"):
        raise ValueError(f"{location} paths must live under the {root} mount")
    return normalized


def host_path(location: str) -> str | None:
    """Host-side folder for a mount (compose env passthrough), if configured."""
    if location not in LOCATIONS:
        return None
    return os.environ.get(LOCATIONS[location][2]) or None


def health_check(path: str) -> dict:
    """Best-effort filesystem health for a path (never raises)."""
    p = Path(path)
    out = {"path": path, "exists": False, "readable": False, "writable": False, "free_bytes": None}
    try:
        out["exists"] = p.is_dir()
        out["readable"] = out["exists"] and os.access(p, os.R_OK)
        out["writable"] = out["exists"] and os.access(p, os.W_OK)
        if out["exists"]:
            out["free_bytes"] = shutil.disk_usage(p).free
    except OSError:
        pass
    return out


def ensure_dir(path: str) -> bool:
    """Best-effort mkdir -p (CI/test environments may lack the mounts)."""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def probe_writable(path: str) -> dict:
    """Write-probe a location: create (if needed) + write/delete a temp file."""
    try:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        tmp = p / f".nestbox-probe-{os.getpid()}"
        tmp.write_text("ok")
        tmp.unlink()
        return {"ok": True, "path": path}
    except OSError as exc:
        return {"ok": False, "path": path, "detail": str(exc)}
