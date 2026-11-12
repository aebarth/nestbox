"""Organization lifecycle helpers shared by first-run setup and the admin API (#10).

create_org_with_defaults() creates the organization, its owner membership,
and the per-org starter data (default record types + built-in Scan Inbox
source) in one transaction. The RLS GUC is set locally so the new org's rows
pass their WITH CHECK policies even though the caller's session is scoped to
a different org (or has no org context at all, as during first-run setup).
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from models import Organization, OrgMember, RecordType, Source


def unique_slug(db: Session, name: str) -> str:
    """Derive a URL/label-safe slug from an org name; suffix on collision."""
    base = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")[:32] or "org"
    slug, n = base, 2
    while db.execute(text("SELECT 1 FROM organizations WHERE slug = :s"), {"s": slug}).scalar():
        suffix = f"-{n}"
        slug = base[: 32 - len(suffix)] + suffix
        n += 1
    return slug


def create_org_with_defaults(
    db: Session,
    name: str,
    kind: str = "personal",
    owner_user_id: int | None = None,
) -> Organization:
    """Create an organization plus its per-org starter data.

    All inserts happen in the caller's transaction (commit/rollback is the
caller's). When owner_user_id is given, an org_members row with role 'owner'
is created alongside. Returns the flushed Organization (id populated).
    """
    org = Organization(name=name, slug=unique_slug(db, name), kind=kind)
    db.add(org)
    db.flush()  # get org.id

    if owner_user_id is not None:
        db.add(OrgMember(user_id=owner_user_id, org_id=org.id, role="owner"))

    # Scope this transaction's RLS context to the new org so its starter
    # rows pass the org_isolation WITH CHECK policies.
    db.execute(text("SET LOCAL app.org_id = :oid"), {"oid": str(org.id)})

    defaults = [
        ("DHR", "Device History Record", None),
        ("DMR", "Device Master Record", None),
        ("TRN", "Training Record", 5),
        ("CAL", "Calibration Record", 5),
        ("SUP", "Supplier Record", 7),
        ("CAPA", "CAPA Record", 7),
        ("GEN", "General Document", None),
    ]
    for code, dname, retention in defaults:
        db.add(RecordType(org_id=org.id, code=code, name=dname, retention_years=retention))
    # Built-in scan inbox source
    db.add(Source(org_id=org.id, name="Scan Inbox", kind="local",
                  config={"root": "/inbox", "builtin_inbox": True}, is_monitored=True))
    return org
