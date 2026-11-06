"""Nestbox admin CLI — database lifecycle (Phase 2.1, #9).

Thin wrapper over services/lifecycle.py so the API and the CLI can never
diverge in behavior. Run inside the backend container:

    docker compose exec backend python cli.py list
    docker compose exec backend python cli.py backup [--notes "..."]
    docker compose exec backend python cli.py restore <backup-id> --confirm
    docker compose exec backend python cli.py reset --confirm
"""
import argparse
import sys

from database import SessionLocal, apply_migrations
from models import Backup
from services import lifecycle


def _list(db) -> None:
    rows = db.query(Backup).order_by(Backup.created_at.desc(), Backup.id.desc()).all()
    if not rows:
        print("No backups yet.")
        return
    for b in rows:
        print(f"{b.id:>5}  {b.filename}  {b.size_bytes:>10} bytes  {b.kind:<9}  sha256={b.sha256[:16]}…  {b.created_at:%Y-%m-%d %H:%M}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="nestbox-cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list backups")

    p_backup = sub.add_parser("backup", help="create a backup now")
    p_backup.add_argument("--notes", default="")

    p_restore = sub.add_parser("restore", help="restore a backup over the live database")
    p_restore.add_argument("backup_id", type=int)
    p_restore.add_argument("--confirm", action="store_true", help="required; types no safety prompt in CLI mode")

    p_reset = sub.add_parser("reset", help="wipe all data and rebuild the schema (takes a pre-reset backup)")
    p_reset.add_argument("--confirm", action="store_true", help="required")

    args = parser.parse_args()

    try:
        apply_migrations()
    except Exception as exc:
        print(f"error: could not reach the database ({exc})", file=sys.stderr)
        return 1

    with SessionLocal() as db:
        try:
            if args.cmd == "list":
                _list(db)
            elif args.cmd == "backup":
                b = lifecycle.create_backup(db, kind="manual", actor_username="cli", notes=args.notes)
                print(f"Backup created: {b.filename} ({b.size_bytes} bytes)")
            elif args.cmd == "restore":
                if not args.confirm:
                    print('refusing: restore replaces the live database; re-run with --confirm', file=sys.stderr)
                    return 1
                result = lifecycle.restore_backup(db, args.backup_id, actor_username="cli")
                print(f"Restored from {result['restored_from']}")
            elif args.cmd == "reset":
                if not args.confirm:
                    print("refusing: reset wipes ALL data; re-run with --confirm", file=sys.stderr)
                    return 1
                result = lifecycle.reset_database(db, actor_username="cli")
                print(f"Database reset. Pre-reset backup kept: {result['pre_reset_backup']}")
        except (LookupError, lifecycle.LifecycleError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
