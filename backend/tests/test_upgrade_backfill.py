# Moved to test_a_upgrade_backfill.py.
#
# The leading "a" makes pytest collect this file BEFORE every other test
# module (pytest runs files in alphabetical order). That ordering is load-
# bearing: these assertions verify the pristine v4 → v5 upgrade state, and
# test_lifecycle.py's reset/restore tests DROP and rebuild the public schema
# mid-suite — after that, the upgraded state no longer exists to assert on.
#
# This stub keeps the old path from silently re-collecting stale copies of
# the tests (the repo tooling here cannot delete files).
