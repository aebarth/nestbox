# legacy-v4 — pre-multi-org schema snapshot (upgrade rehearsal only)

A frozen copy of the fresh-install DDL as of schema v4 (commit `684c182`,
before multi-organization support landed in #10), plus `99-legacy-data.sql`
which simulates an install that has been in use: starter lookups, a Scan
Inbox source, projects, boxes, records, a duplicate group, a folder rule,
a scan run, a file event and an alert.

**Do not point a real install at this.** It exists so CI's
`backend-tests-upgraded` job can build a test database the way an existing
customer's would look, let the app run migrations `005_multi_org.sql` +
`006_fix_rls_policies.sql` on startup, and then run the full suite (RLS
isolation matrix included) against the upgraded schema.

Files are applied in sorted order by `backend/tests/conftest.py` when
`NESTBOX_TEST_INIT_DIR=db-init/legacy-v4` is set. Keep the `NN-name.sql`
numbering if you add a fixture file.
