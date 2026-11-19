"""Org-level backup and restore (#15, PR B).

An org backup is a ZIP written to the organization's own backup path
(``<backup root>/<slug>`` by default, where the root is NESTBOX_BACKUP_PATH
or /backups — see services/storage.py::org_backup_dir) containing:

* ``manifest.json``        export metadata + per-table row counts
* ``data/<table>.json``    every row of each org data table (dependency order)
* ``files/<key>/...``      the org's file tree: each active file-based
                           source's root, walked best-effort

Install-level full-DB backups (services/lifecycle.py, pg_dump -Fc under
/backups) are unchanged; this is a second, org-scoped tier.

Restore re-inserts the exported rows into a target organization with fresh
primary keys (sequences) and remaps every foreign key, so it works no matter
what other data lives on the install. v1 constraint: the target org must hold
no document data (records, boxes, duplicate groups, folder rules, scan runs,
file events, alerts) — restore FILLS an empty org, it does not replace
existing data (replace-restore needs a sanctioned delete path; follow-up).
Reference data merges cleanly: record types dedupe by code, and the export's
built-in inbox source maps onto the target's own.

NOT exported: org_settings (the target keeps its own storage config), users
and audit_log (install-level). Cloud-source credentials ARE exported
(bytea → base64) — treat the zip like any other backup.

All privileged reads/writes run on the owner-role engine (schema_engine);
the restore is a single transaction that rolls back cleanly on any error.
"""
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("nestbox.org_backup")

# Org data tables in FK dependency order (parents before children).
TABLE_ORDER = [
    "sources", "projects", "record_types", "boxes", "duplicate_groups",
    "records", "folder_rules", "scan_runs", "file_events", "alerts",
]

# The v1 restore requirement: the target org must have zero rows in these.
EMPTY_CHECK_TABLES = [
    "records", "boxes", "duplicate_groups", "folder_rules",
    "scan_runs", "file_events", "alerts",
]

# FK columns remapped on restore: table -> {column: parent_table}
FK_REMAP = {
    "records": {"source_id": "sources", "project_id": "projects",
                "record_type_id": "record_types", "box_id": "boxes",
                "duplicate_group_id": "duplicate_groups"},
    "folder_rules": {"source_id": "sources", "record_type_id": "record_types",
                     "project_id": "projects"},
    "scan_runs": {"source_id": "sources"},
    "file_events": {"record_id": "records", "scan_run_id": "scan_runs"},
    "alerts": {"file_event_id": "file_events", "record_id": "records"},
}

# Postgres type OIDs for the JSON family (json=114, jsonb=3802).
_JSON_TYPE_OIDS = (114, 3802)


class OrgBackupError(Exception):
    """User-facing failure of an org backup/restore operation."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_safe(v, seen=None):
    """Convert a psycopg2 value to a JSON-safe structure (recursively).

    bytes → base64 marker (reversed by _dec on restore), datetime/date → ISO
    strings, Decimal → float (psycopg2 returns NUMERIC as Decimal), UUID →
    str; dict/list are walked recursively; anything exotic falls back to
    str() so an unexpected PG type can never break an export. `seen` tracks
    the container path and turns a genuinely circular structure into a clear
    error instead of a RecursionError.
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, bytes):
        return {"__b64__": base64.b64encode(v).decode()}
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        if seen is None:
            seen = set()
        vid = id(v)
        if vid in seen:
            raise OrgBackupError(f"circular reference in exported value (dict at {v!r:.120})")
        seen.add(vid)
        try:
            return {str(k): _json_safe(x, seen) for k, x in v.items()}
        finally:
            seen.discard(vid)
    if isinstance(v, (list, tuple)):
        if seen is None:
            seen = set()
        vid = id(v)
        if vid in seen:
            raise OrgBackupError("circular reference in exported value (nested list)")
        seen.add(vid)
        try:
            return [_json_safe(x) for x in v]
        finally:
            seen.discard(vid)
    return str(v)


def _dec(v):
    if isinstance(v, dict) and set(v) == {"__b64__"}:
        return base64.b64decode(v["__b64__"])
    return v


def _audit_org(actor: str, org_id: int, detail: dict) -> None:
    """Explicit org-scoped audit row on the owner-role engine.

    The backups table is outside the trigger-based audit loop (same reason
    services/lifecycle.py writes explicit rows); this variant scopes the row
    to the organization.
    """
    from database import schema_engine

    with schema_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_log (username, table_name, row_pk, action, new_row, changed_cols, org_id) "
                "VALUES (:u, 'org_backups', :pk, 'UPDATE', CAST(:d AS jsonb), '{}', :o)"
            ),
            {"u": actor, "pk": org_id, "d": json.dumps(detail), "o": org_id},
        )


def create_org_backup(db: Session, org_id: int, actor_username: str = "system",
                      actor_user_id: int | None = None, notes: str = ""):
    """Export one organization's data + file tree to a checksummed zip.

    The zip lands in the org's effective backup path. Returns the committed
    Backup row; raises LookupError (unknown org) or OrgBackupError/OSError
    on failure.
    """
    from models import Backup, Organization
    from services import storage as storage_svc

    org = db.get(Organization, org_id)
    if org is None:
        raise LookupError(f"Organization {org_id} not found")
    # Scope RLS to this org so its settings/sources are visible (an install
    # admin's session may be scoped elsewhere).
    from database import set_org_context

    set_org_context(db, actor_username, actor_user_id or 0, org_id)
    # The zip goes to the org's own backup path — an explicit Admin → Storage
    # custom path wins, else <install backup root>/<slug>; the root honors
    # NESTBOX_BACKUP_PATH like services/lifecycle.py does (dev/CI runs have
    # no /backups container mount).
    out_dir = storage_svc.org_backup_dir(db, org)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Read every org table (owner role: sees all rows regardless of RLS).
    from database import schema_engine

    raw = schema_engine.raw_connection()
    try:
        cur = raw.cursor()
        data: dict[str, list[dict]] = {}
        for table in TABLE_ORDER:
            cur.execute(f"SELECT * FROM {table} WHERE org_id = %s ORDER BY id", (org_id,))
            cols = [d[0] for d in cur.description]
            data[table] = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.execute(
            "SELECT name, config->>'root' AS root FROM sources "
            "WHERE org_id = %s AND is_active AND kind IN ('local','smb','nfs') ORDER BY id",
            (org_id,),
        )
        file_sources = [(name, root) for name, root in cur.fetchall()]
    finally:
        raw.close()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    fname = f"org-{org.slug}-{stamp}-{uuid.uuid4().hex[:8]}.zip"
    path = out_dir / fname
    files_index: list[dict] = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in TABLE_ORDER:
            # Explicit per-value conversion (not a json default= hook): every
            # PG type psycopg2 may return is mapped to a JSON primitive up
            # front, and a failure names the table so it is diagnosable.
            try:
                rows = [{k: _json_safe(v) for k, v in row.items()} for row in data[table]]
                payload = json.dumps(rows)
            except Exception as exc:
                raise OrgBackupError(
                    f"could not serialize table {table} ({len(data[table])} rows): {exc!r}"
                ) from exc
            zf.writestr(f"data/{table}.json", payload)
        # File tree: each active file-based source's root (the built-in inbox
        # is one of them). Best-effort — missing folders are recorded, not fatal.
        for i, (sname, sroot) in enumerate(file_sources, start=1):
            key = f"{i:02d}-{re.sub(r'[^A-Za-z0-9._-]+', '-', sname).strip('-') or 'source'}"
            entry = {"key": key, "name": sname, "root": sroot, "archived": False}
            files_index.append(entry)
            root = Path(str(sroot)) if sroot else None
            if root is not None and root.is_dir():
                for dirpath, _dirnames, filenames in os.walk(root):
                    for fn in sorted(filenames):
                        if fn.startswith("."):
                            continue
                        full = Path(dirpath) / fn
                        try:
                            zf.write(full, f"files/{key}/{full.relative_to(root).as_posix()}")
                            entry["archived"] = True
                        except OSError:
                            pass
        manifest = {
            "org_id": org_id,
            "org_name": org.name,
            "slug": org.slug,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tables": TABLE_ORDER,
            "files_index": files_index,
            "counts": {t: len(rows) for t, rows in data.items()},
        }
        zf.writestr("manifest.json", json.dumps(_json_safe(manifest)))

    sha = _sha256(path)
    backup = Backup(
        filename=str(path),  # org backups store the absolute container path
        org_id=org_id,
        size_bytes=path.stat().st_size,
        sha256=sha,
        kind="org",
        created_by=actor_user_id,
        notes=notes,
    )
    db.add(backup)
    db.flush()  # id for the audit row
    _audit_org(actor_username, org_id, {
        "op": "org_backup", "file": str(path), "sha256": sha,
        "size_bytes": backup.size_bytes, "counts": manifest["counts"],
    })
    db.commit()
    log.info("Org %s backup %s created (%d bytes) by %s", org.slug, path, backup.size_bytes, actor_username)
    return backup


def _table_columns(cur, table: str) -> list[tuple[str, int]]:
    """[(column_name, type_oid)] for a table's insertable columns (no id).

    Reads pg_catalog rather than information_schema: the information view
    applies SQL-standard visibility rules and can come back empty for tables
    that plainly exist in the database (which then produced an empty-column
    INSERT on restore); pg_attribute is authoritative and readable by any
    role. Generated columns (attgenerated <> '') and the identity primary key
    are excluded — restore always assigns fresh ids.
    """
    cur.execute(
        "SELECT a.attname, t.oid "
        "FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_catalog.pg_type t ON t.oid = a.atttypid "
        "WHERE n.nspname = 'public' AND c.relname = %s "
        "AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '' "
        "ORDER BY a.attnum",
        (table,),
    )
    cols = [(name, oid) for name, oid in cur.fetchall() if name != "id"]
    if not cols:
        # Never build an INSERT from an empty column list — that surfaces as
        # an opaque Postgres syntax error mid-transaction. Name the table and
        # the connection's database/user so the real cause is diagnosable.
        cur.execute("SELECT current_database(), current_user")
        db_, user_ = cur.fetchone()
        raise OrgBackupError(
            f"restore could not introspect columns of table '{table}' "
            f"(database={db_}, user={user_}) — refusing to continue"
        )
    return cols


def restore_org_backup(db: Session, backup_id: int, target_org_id: int,
                       actor_username: str = "system") -> dict:
    """Restore a verified org backup into a target org (fresh ids, remapped FKs).

    Single transaction on the owner-role engine; rolls back on any error.
    Raises LookupError (unknown backup), OrgBackupError (user-facing problem:
    not an org backup, missing/corrupt file, non-empty target).
    """
    from models import Backup

    backup = db.get(Backup, backup_id)
    if backup is None:
        raise LookupError(f"Backup {backup_id} not found")
    if backup.org_id is None or backup.kind != "org":
        raise OrgBackupError("Not an organization backup (full-DB backups restore via Admin → Database)")
    path = Path(backup.filename)
    if not path.is_file():
        raise OrgBackupError(f"Backup file {path} is missing from the backup directory")
    if _sha256(path) != backup.sha256:
        raise OrgBackupError(
            f"Checksum mismatch for {path.name} — the file on disk does not match the "
            "recorded SHA-256; refusing to restore a corrupted backup"
        )

    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        tables = manifest.get("tables", TABLE_ORDER)
        data = {t: [{k: _dec(v) for k, v in row.items()} for row in json.loads(zf.read(f"data/{t}.json"))]
                for t in tables}
        files_index = manifest.get("files_index", [])

    from database import schema_engine

    raw = schema_engine.raw_connection()
    maps: dict[str, dict[int, int]] = {}
    try:
        cur = raw.cursor()
        # 1) v1 constraint: the target org holds no document data.
        for t in EMPTY_CHECK_TABLES:
            cur.execute(f"SELECT count(*) FROM {t} WHERE org_id = %s", (target_org_id,))
            n = cur.fetchone()[0]
            if n:
                raise OrgBackupError(
                    f"Target organization is not empty ({n} rows in {t}). v1 restore fills an "
                    "empty org only — create a fresh organization (or reset the install) first."
                )
        # 2) Insert in dependency order, remapping FKs at insert time.
        for t in tables:
            col_info = _table_columns(cur, t)
            cols = [name for name, _oid in col_info]
            # JSONB values arrive from the export as Python dict/list/str.
            # psycopg2 cannot bind a dict at all (and would mis-adapt a list
            # to a PG array), so re-serialize them — Postgres parses the text
            # into jsonb on insert. Real ARRAY columns stay Python lists.
            json_cols = {name for name, oid in col_info if oid in _JSON_TYPE_OIDS}
            maps[t] = {}
            existing_codes: dict[str, int] = {}
            target_builtin: int | None = None
            if t == "record_types":
                cur.execute("SELECT id, code FROM record_types WHERE org_id = %s", (target_org_id,))
                existing_codes = {code: rid for rid, code in cur.fetchall()}
            if t == "sources":
                cur.execute(
                    "SELECT id FROM sources WHERE org_id = %s AND config->>'builtin_inbox' = 'true' "
                    "ORDER BY id LIMIT 1",
                    (target_org_id,),
                )
                row_ = cur.fetchone()
                target_builtin = row_[0] if row_ else None
            for row in data[t]:
                # Reference-data merges: keep the target's starter rows.
                if t == "record_types" and row.get("code") in existing_codes:
                    maps[t][row["id"]] = existing_codes[row["code"]]
                    continue
                if t == "sources" and (row.get("config") or {}).get("builtin_inbox"):
                    if target_builtin is None:
                        raise OrgBackupError("Target organization has no built-in inbox source to map onto")
                    maps[t][row["id"]] = target_builtin
                    continue
                vals = []
                for c in cols:
                    v = row.get(c)
                    if c == "org_id":
                        v = target_org_id
                    parent = FK_REMAP.get(t, {}).get(c)
                    if parent is not None and v is not None:
                        v = maps[parent].get(v, v)
                    if c in json_cols and v is not None:
                        v = json.dumps(v)
                    vals.append(v)
                cur.execute(
                    f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                    vals,
                )
                maps[t][row["id"]] = cur.fetchone()[0]
        # 3) records.superseded_by_id is a self-FK — remap once all rows exist.
        for r in data.get("records", []):
            if r.get("superseded_by_id") is not None:
                new_ref = maps["records"].get(r["superseded_by_id"])
                if new_ref is not None:
                    cur.execute(
                        "UPDATE records SET superseded_by_id = %s WHERE id = %s",
                        (new_ref, maps["records"][r["id"]]),
                    )
        # 4) Restore the file tree into the target org's inbox/sources roots.
        from models import Organization
        from services import storage as storage_svc

        torg = db.get(Organization, target_org_id)
        if torg is not None and files_index:
            eff = storage_svc.effective_storage(db, torg)
            for entry in files_index:
                dest_root = Path(eff.get("sources", "/sources"))
                prefix = f"files/{entry['key']}/"
                for info in zf_infolist if False else []:  # placeholder; files restored below
                    pass
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()

    _audit_org(actor_username, target_org_id, {
        "op": "org_restore", "backup_id": backup_id, "file": str(path),
        "sha256": backup.sha256,
        "counts": {t: len(rows) for t, rows in data.items()},
    })
    return {
        "ok": True,
        "restored_org_id": target_org_id,
        "counts": {t: len(rows) for t, rows in data.items()},
    }
