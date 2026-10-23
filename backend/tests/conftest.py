"""
Test configuration and shared fixtures for the PRism backend test suite.

ISOLATION GUARANTEE
-------------------
All tests must use an ephemeral SupabaseService instance (persist_local=False).
The production file `data/local_repositories.json` MUST NOT be touched by any test.

The `patch_supabase_service` autouse fixture enforces this by:
  1. Overriding FastAPI's dependency injection for HTTP-level tests
     via `app.dependency_overrides`.
  2. Replacing the module-level `_supabase_service` singleton in
     `app.services.supabase` so that any call to `get_supabase_service()`
     — regardless of whether the test imported the function at module level
     or calls it inline — returns the ephemeral instance.
"""
import pytest

import app.services.supabase as _supabase_module
from app.main import app
from app.services.supabase import SupabaseService, get_supabase_service


@pytest.fixture
def isolated_supabase():
    """
    Returns a fresh, fully in-memory SupabaseService instance.

    persist_local=False ensures zero file I/O to data/local_repositories.json.
    Each test function gets its own blank instance — no shared state between tests.
    """
    return SupabaseService(supabase_url=None, supabase_key=None, persist_local=False)


@pytest.fixture(autouse=True)
def patch_supabase_service(request):
    """
    Autouse fixture: replaces the global SupabaseService singleton with an
    ephemeral, non-persisting instance for every test in this suite.

    Two injection points are covered:
      A. FastAPI dependency injection (Depends(get_supabase_service)) via
         app.dependency_overrides — protects all HTTP router handlers.
      B. The module-level `_supabase_service` singleton in app.services.supabase.
         Because `get_supabase_service()` reads this global at call time, replacing
         it here intercepts ALL call sites — including test bodies that did
         `from app.services.supabase import get_supabase_service` at module level.

    Tests marked @pytest.mark.live_integration bypass this fixture and receive
    the real configured singleton (opt-in integration tests requiring real credentials).
    """
    if request.node.get_closest_marker("live_integration"):
        yield
        return

    ephemeral = SupabaseService(supabase_url=None, supabase_key=None, persist_local=False)

    # Save the current singleton (may be None or a real instance)
    _saved_singleton = _supabase_module._supabase_service

    # Replace the singleton so get_supabase_service() returns our ephemeral instance
    _supabase_module._supabase_service = ephemeral

    # Also override FastAPI DI for endpoint-level tests
    app.dependency_overrides[get_supabase_service] = lambda: ephemeral

    try:
        yield ephemeral
    finally:
        # Restore original singleton and clear DI override
        _supabase_module._supabase_service = _saved_singleton
        app.dependency_overrides.pop(get_supabase_service, None)
