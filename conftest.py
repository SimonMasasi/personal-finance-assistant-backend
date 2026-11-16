"""Root conftest.

This runs before `src/core/config.py` is imported anywhere, which is the only window in
which the test environment can be pinned. `setdefault` rather than assignment so
a real environment variable (CI) still wins over these defaults.
"""

import os

# A separate database from the dev one — the suite truncates between tests.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://pfa:pfa@localhost:5433/pfa_test"
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-at-least-32-chars")
os.environ.setdefault("DEBUG", "False")

# The suite creates far more accounts and logins per minute than a human would.
# Rate limiting is verified separately in test_hardening.py by inspecting the
# configured limits rather than by trying to trip them.
os.environ.setdefault("RATE_LIMIT_GOOGLE", "10000/minute")
os.environ.setdefault("RATE_LIMIT_REFRESH", "10000/minute")

# Required in production, so it must be pinned here or collection dies at
# src/core/config.py import. The value is the audience the fake tokens are minted for.
os.environ.setdefault(
    "GOOGLE_CLIENT_IDS", "test-web-client.apps.googleusercontent.com"
)

# Small enough to exercise the paging path without inserting 500 rows.
os.environ.setdefault("SYNC_MAX_PULL", "5")
