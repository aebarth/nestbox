from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(Text, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict | list | str | int | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class Source(Base, TimestampMixin):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    credentials_enc: Mapped[bytes | None] = mapped_column(BYTEA)
    is_monitored: Mapped[bool] = mapped_column(Boolean, default=True)
    scan_cron: Mapped[str | None] = mapped_column(Text)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class RecordType(Base, TimestampMixin):
    __tablename__ = "record_types"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    retention_years: Mapped[int | None] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Box(Base, TimestampMixin):
    __tablename__ = "boxes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    box_number: Mapped[str] = mapped_column(Text, unique=True)
    physical_location: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="open")
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DuplicateGroup(Base, TimestampMixin):
    __tablename__ = "duplicate_groups"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    match_kind: Mapped[str] = mapped_column(Text)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    dismissed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Record(Base, TimestampMixin):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[str] = mapped_column(UUID(as_uuid=False), server_default="gen_random_uuid()")
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hash_algo: Mapped[str | None] = mapped_column(Text)
    hash_value: Mapped[str | None] = mapped_column(Text)
    hashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    record_type_id: Mapped[int | None] = mapped_column(ForeignKey("record_types.id"))
    lot_number: Mapped[str] = mapped_column(Text, default="")
    part_number: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    box_id: Mapped[int | None] = mapped_column(ForeignKey("boxes.id"))
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="active")
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("records.id"))
    retention_until: Mapped[date | None] = mapped_column(Date)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    duplicate_group_id: Mapped[int | None] = mapped_column(ForeignKey("duplicate_groups.id"))
    ocr_status: Mapped[str] = mapped_column(Text, default="pending")
    ocr_method: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_fingerprint: Mapped[str | None] = mapped_column(Text)
    ocr_tsv = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', left(coalesce(ocr_text, ''), 200000))", persisted=True),
    )

    source: Mapped[Source] = relationship()
    project: Mapped[Project | None] = relationship()
    record_type: Mapped[RecordType | None] = relationship()
    box: Mapped[Box | None] = relationship()


class FolderRule(Base, TimestampMixin):
    __tablename__ = "folder_rules"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    folder_prefix: Mapped[str] = mapped_column(Text, default="")
    record_type_id: Mapped[int | None] = mapped_column(ForeignKey("record_types.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    kind: Mapped[str] = mapped_column(Text, default="scan")
    trigger: Mapped[str] = mapped_column(Text, default="manual")
    status: Mapped[str] = mapped_column(Text, default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class FileEvent(Base):
    __tablename__ = "file_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id"))
    scan_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"))
    event_type: Mapped[str] = mapped_column(Text)
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_event_id: Mapped[int | None] = mapped_column(ForeignKey("file_events.id"))
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id"))
    alert_type: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, default="info")
    status: Mapped[str] = mapped_column(Text, default="open")
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="")

    record: Mapped[Record] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    username: Mapped[str] = mapped_column(Text)
    table_name: Mapped[str] = mapped_column(Text)
    row_pk: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(Text)
    old_row: Mapped[dict | None] = mapped_column(JSONB)
    new_row: Mapped[dict | None] = mapped_column(JSONB)
    changed_cols: Mapped[list[str]] = mapped_column(ARRAY(Text))
