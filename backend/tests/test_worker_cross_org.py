"""Worker cross-org scan isolation (#10 acceptance criterion).

The worker connects as nestbox_admin (BYPASSRLS) so it can claim queued runs
from every org; execute_run must still confine each run to its own org's
sources, and every row it writes (records, file_events, alerts) must carry
that org's org_id. Also covers the concurrency clause: two workers draining
a mixed queue at once never double-claim a run and don't interfere with
each other's org.

Runs are enqueued through the public API (POST /api/scans) so they carry the
caller's org exactly as "Scan Now" produces them; execution uses the real
worker primitives (claim_next_run + execute_run on WorkerSessionLocal).
"""
import json
import os
import threading
import uuid

import pytest
from sqlalchemy import text


def write_file(path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture()
def org_b(db_session):
    """A second organization, created outside RLS (same pattern as
    test_rls_isolation)."""
    from models import Organization

    org = Organization(name="Worker Org B", slug=f"wk-b-{uuid.uuid4().hex[:8]}", kind="business")
    db_session.add(org)
    db_session.commit()
    return org.id


@pytest.fixture()
def client_b(admin_client, org_b):
    """A fresh TestClient logged in as an owner of org B."""
    from fastapi.testclient import TestClient

    from main import app

    r = admin_client.post("/api/admin/users", json={
        "username": f"wk-{uuid.uuid4().hex[:6]}", "password": "wkbpass123",
        "full_name": "Org B Worker Admin", "role": "user",
        "org_id": org_b, "org_role": "owner",
    })
    assert r.status_code == 200, r.text
    username = r.json()["username"]

    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"username": username, "password": "wkbpass123"})
        assert login.status_code == 200, login.text
        assert login.json()["needs_org_selection"] is False  # single org → auto-select
        yield c


@pytest.fixture()
def two_org_env(tmp_path, admin_client, client_b, org_b, db_session):
    """One local source per org (inserted as the table owner, like a migrated
    row), each with its own files on disk. File names and contents carry a
    unique tag so the tests stay independent of data left by other tests in
    the shared session database."""
    os.environ["NESTBOX_SOURCE_ROOTS"] = str(tmp_path)

    from database import schema_engine

    tag = uuid.uuid4().hex[:8]
    root_a, root_b = tmp_path / "orgA", tmp_path / "orgB"
    write_file(root_a / f"alpha-{tag}-1.pdf", f"ORG A DOCUMENT ONE {tag}".encode())
    write_file(root_a / f"alpha-{tag}-2.pdf", f"ORG A DOCUMENT TWO {tag}".encode())
    write_file(root_b / f"beta-{tag}-1.pdf", f"ORG B DOCUMENT ONE {tag}".encode())
    write_file(root_b / f"beta-{tag}-2.pdf", f"ORG B DOCUMENT TWO {tag}".encode())

    default_org_id = db_session.execute(text(
        "SELECT id FROM organizations ORDER BY created_at LIMIT 1"
    )).scalar_one()

    def make_source(org_id: int, root) -> int:
        with schema_engine.begin() as conn:
            return conn.execute(text(
                "INSERT INTO sources (org_id, name, kind, config) "
                "VALUES (:o, :n, 'local', CAST(:c AS jsonb)) RETURNING id"
            ), {
                "o": org_id,
                "n": f"wk-{tag}-{uuid.uuid4().hex[:6]}",
                "c": json.dumps({"root": str(root)}),
            }).scalar_one()

    return {
        "tag": tag,
        "org_a": default_org_id,
        "org_b": org_b,
        "src_a": make_source(default_org_id, root_a),
        "src_b": make_source(org_b, root_b),
    }


def queue_scan(client, source_id: int) -> dict:
    r = client.post(f"/api/scans?source_id={source_id}")
    assert r.status_code == 200, r.text
    return r.json()


def drain_worker() -> list:
    """One pass of the worker loop: claim + execute until the queue is empty.

    Returns the run ids this worker executed. UPDATE ... FOR UPDATE SKIP
    LOCKED in claim_next_run makes double-claims impossible, so the union of
    both workers' lists must equal the queued runs exactly once each.
    """
    from database import WorkerSessionLocal
    from services.scanner import execute_run
    from worker import claim_next_run

    executed = []
    while True:
        with WorkerSessionLocal() as db:
            run = claim_next_run(db)
            if run is None:
                break
            execute_run(db, run)
            executed.append(run.id)
    return executed


def scanned_records(tag: str) -> dict:
    """Ground truth via the table owner: {record name: org_id} for this
    test's scanned files."""
    from database import schema_engine

    with schema_engine.connect() as conn:
        return {
            name: org_id
            for name, org_id in conn.execute(
                text("SELECT name, org_id FROM records WHERE name LIKE :t"),
                {"t": f"%{tag}%"},
            ).all()
        }


def test_worker_run_stays_in_its_org(two_org_env, admin_client, client_b):
    env = two_org_env

    run_a = queue_scan(admin_client, env["src_a"])
    run_b = queue_scan(client_b, env["src_b"])

    from database import schema_engine

    # The API stamped each run with the CALLER's org (ScanRunOut doesn't
    # expose org_id, so verify against the table itself).
    with schema_engine.connect() as conn:
        stamped = dict(conn.execute(text(
            "SELECT id, org_id FROM scan_runs WHERE id IN (:a, :b)"
        ), {"a": run_a["id"], "b": run_b["id"]}).all())
    assert stamped == {run_a["id"]: env["org_a"], run_b["id"]: env["org_b"]}

    executed = drain_worker()
    assert sorted(executed) == sorted([run_a["id"], run_b["id"]])

    with schema_engine.connect() as conn:
        for rid in (run_a["id"], run_b["id"]):
            status, stats = conn.execute(
                text("SELECT status, stats FROM scan_runs WHERE id = :i"), {"i": rid}
            ).one()
            assert status == "done"
            assert stats["new"] == 2  # only its own source's two files

        # Every event the scan wrote carries the same org as its record.
        mismatched = conn.execute(text(
            "SELECT count(*) FROM file_events e JOIN records r ON r.id = e.record_id "
            "WHERE r.name LIKE :t AND e.org_id <> r.org_id"
        ), {"t": f"%{env['tag']}%"}).scalar()
        assert mismatched == 0

    recs = scanned_records(env["tag"])
    assert recs[f"alpha-{env['tag']}-1.pdf"] == env["org_a"]
    assert recs[f"alpha-{env['tag']}-2.pdf"] == env["org_a"]
    assert recs[f"beta-{env['tag']}-1.pdf"] == env["org_b"]
    assert recs[f"beta-{env['tag']}-2.pdf"] == env["org_b"]

    # RLS visibility backstop: each org's app session sees its own files and
    # none of the other org's.
    from database import SessionLocal, set_org_context

    for oid, prefix in ((env["org_a"], "alpha-"), (env["org_b"], "beta-")):
        with SessionLocal() as s:
            set_org_context(s, "pytest", 1, oid)
            names = set(s.execute(text("SELECT name FROM records")).scalars())
        own = {n for n in names if f"{prefix}{env['tag']}" in n}
        other = "beta-" if prefix == "alpha-" else "alpha-"
        assert own == {f"{prefix}{env['tag']}-1.pdf", f"{prefix}{env['tag']}-2.pdf"}
        assert not any(f"{other}{env['tag']}" in n for n in names)


def test_concurrent_workers_mixed_queue(two_org_env, admin_client, client_b):
    env = two_org_env

    run_a = queue_scan(admin_client, env["src_a"])
    run_b = queue_scan(client_b, env["src_b"])

    results: dict = {}

    def worker(name: str):
        results[name] = drain_worker()

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("w1", "w2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a worker thread hung"

    # Both runs executed exactly once across the two workers — no double-
    # claim, no lost run.
    all_executed = results["w1"] + results["w2"]
    assert sorted(all_executed) == sorted([run_a["id"], run_b["id"]])

    from database import schema_engine

    with schema_engine.connect() as conn:
        statuses = dict(conn.execute(text(
            "SELECT id, status FROM scan_runs WHERE id IN (:a, :b)"
        ), {"a": run_a["id"], "b": run_b["id"]}).all())
    assert statuses == {run_a["id"]: "done", run_b["id"]: "done"}

    # Concurrent scans across the two orgs didn't mix data.
    recs = scanned_records(env["tag"])
    assert recs[f"alpha-{env['tag']}-1.pdf"] == env["org_a"]
    assert recs[f"beta-{env['tag']}-2.pdf"] == env["org_b"]
