from database import SessionLocal
from models import Setting


def get_setting(key: str, default=None):
    with SessionLocal() as db:
        row = db.get(Setting, key)
        return row.value if row is not None else default


def get_secret_setting(key: str) -> str | None:
    """Settings stored Fernet-encrypted (cloud client secrets)."""
    from services.crypto import decrypt_json

    raw = get_setting(key)
    if not raw:
        return None
    data = decrypt_json(raw.encode() if isinstance(raw, str) else raw)
    return data.get("secret") if data else None
