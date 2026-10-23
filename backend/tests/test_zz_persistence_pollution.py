"""
Regression test: verifies that running the test suite does NOT modify
data/local_repositories.json.

This test guards against test fixture leakage into the production
persistent repository store. It must remain the LAST file in pytest's
collection order (alphabetically after all other test_*.py files) so it
validates state after all other tests have run.

If this test fails, a test somewhere created a SupabaseService with
persist_local=True (the default) and called connect_repository or
_save_local_state. Fix: ensure all SupabaseService instances in tests
use persist_local=False, OR are covered by the conftest autouse fixture.
"""
import hashlib
import json
from pathlib import Path

import pytest

_REPOS_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "local_repositories.json"
)

# IDs of repositories that are legitimately connected by the user.
# This set must be updated manually when a real repo is connected or disconnected.
_ALLOWED_REPO_IDS = {
    "1ba7a024-e675-4e2f-94c3-1aeaab5de96e",  # vectorize-io/hindsight (canonical)
    "b97d73a4-5ffb-4d4a-89e2-c20ecc47e15a",  # vectorize-io/hindsight (active session)
    "1134d8ee-5962-42b1-bbec-a35a0d022b36",  # vectorize-io/hindsight (user session)
    "a3f994f3-477d-402d-93e5-95e99dd503a4",  # octocat/Spoon-Knife (user session)
    "358e4f3e-ac1d-4c77-9e3b-b982307a1c0e",  # ronaksarda/microsoft-hindsight (user session)
}


def test_no_test_repositories_leaked_into_persistent_store():
    """
    Asserts that local_repositories.json contains ONLY known, user-connected
    repositories — no test fixtures, no mock repos, no temp data.
    """
    if not _REPOS_FILE.exists():
        # File doesn't exist → nothing leaked
        return

    with open(_REPOS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert isinstance(data, dict), "local_repositories.json must be a JSON object"

    leaked = set(data.keys()) - _ALLOWED_REPO_IDS
    assert not leaked, (
        f"TEST DATA LEAKED INTO local_repositories.json!\n"
        f"Unexpected repository IDs found: {leaked}\n"
        f"Full leaked entries:\n"
        + "\n".join(
            f"  {rid}: {data[rid].get('full_name', '(no full_name)')}"
            for rid in leaked
        )
        + "\n\nFix: ensure conftest.py autouse fixture covers ALL SupabaseService "
        "instances created in tests."
    )


def test_real_repository_still_present():
    """
    Asserts that the real vectorize-io/hindsight repository record is intact
    and has not been accidentally deleted or corrupted.
    """
    if not _REPOS_FILE.exists():
        pytest.fail(f"local_repositories.json does not exist at {_REPOS_FILE}")

    with open(_REPOS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    matching = [r for r in data.values() if r.get("full_name") == "vectorize-io/hindsight"]
    assert len(matching) > 0, (
        "vectorize-io/hindsight is missing from local_repositories.json. "
        "It may have been accidentally deleted during a test cleanup."
    )

    repo = matching[0]
    assert repo["full_name"] == "vectorize-io/hindsight"
    assert repo["github_repo_id"] == 1086419061
    assert repo["webhook_active"] is False
