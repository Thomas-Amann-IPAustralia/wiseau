"""Pytest configuration for the backend test suite.

Living at the backend root, this file makes pytest add ``backend/`` to
``sys.path`` so tests can ``import main`` and ``import parsers`` the same way
the application does at runtime, without an editable install.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear the per-IP rate-limit counters around every test.

    The default limits (``60/minute``, ``1000/day``) are enforced on every route
    by ``SlowAPIMiddleware``, and every ``TestClient`` request arrives from the
    same client address — so without this the suite's own request volume would
    eventually 429 whichever unrelated test happened to run late. Resetting also
    keeps the tests that *assert* a limit from leaking into their neighbours.
    """
    from main import limiter

    limiter.reset()
    yield
    limiter.reset()
