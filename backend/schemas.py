from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth / users ----

class LoginIn(BaseModel):
    username: str
    password: str


class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = ""
    org_name: str = ""


class UserOut(ORMModel):
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = ""
    role: str = "user"


class UserUpdateIn(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


# ---- lookups ----

class LookupIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str
    retention_years: int | None = None  # record types only
    is_active: bool = True


class ProjectOut(ORMModel):
    id: int
    code: str
    name: str
    is_active: bool


class RecordTypeOut(ORMModel):
    id: int
    code: str
    name: str
    retention_years: int | None
    is_active: bool


# ---- sources ----

class SourceIn(BaseModel):
    name: str
    kind: str = "local"
    config: dict = {}
    is_monitored: bool = True
    scan_cron: str | None = None
    is_active: bool = True


class SourceOut(ORMModel):
    id: int
    name: str
    kind: str
    config: dict
    is_monitored: bool
    scan_cron: str | None
    last_scan_at: datetime | None
    is_active: bool
    has_credentials: bool = False


# ---- boxes ----

class BoxIn(BaseModel):
    box_number: str
    physical_location: str = ""
    description: str = ""
    status: str = "open"


class BoxOut(ORMModel):
    id: int
    box_number: str
    physical_location: str
    description: str
    status: str
    sealed_at: datetime | None
    record_count: int = 0


# ---- records ----

class RecordCategorizeIn(BaseModel):
    title: str | None = None
    project_id: int | None = None
    record_type_id: int | None = None
    lot_number: str | None = None
    part_number: str | None = None
    tags: list[str] | None = None
    notes: str | None = None
    box_id: int | None = None


class RecordStatusIn(BaseModel):
    status: str  # retired | superseded | active
    superseded_by_id: int | None = None
    reason: str = ""


class RecordOut(ORMModel):
    id: int
    uuid: str
    source_id: int
    source_name: str | None = None
    external_id: str | None
    path: str
    name: str
    size_bytes: int
    file_mtime: datetime | None
    hash_algo: str | None
    hash_value: str | None
    title: str
    project_id: int | None
    project_code: str | None = None
    record_type_id: int | None
    record_type_code: str | None = None
    lot_number: str
    part_number: str
    tags: list[str]
    notes: str
    box_id: int | None
    box_number: str | None = None
    filed_at: datetime | None
    status: str
    retention_until: date | None
    first_seen_at: datetime
    last_seen_at: datetime
    duplicate_group_id: int | None


class FileEventOut(ORMModel):
    id: int
    event_type: str
    old_value: dict | None
    new_value: dict | None
    occurred_at: datetime


# ---- scans / alerts ----

class ScanRunOut(ORMModel):
    id: int
    source_id: int | None
    kind: str
    trigger: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    stats: dict
    error_text: str | None
    created_at: datetime


class AlertOut(ORMModel):
    id: int
    record_id: int
    record_name: str | None = None
    record_path: str | None = None
    source_name: str | None = None
    alert_type: str
    severity: str
    status: str
    resolution: str | None
    notes: str
    created_at: datetime


class AlertResolveIn(BaseModel):
    status: str  # acknowledged | resolved
    resolution: str | None = None
    notes: str = ""
