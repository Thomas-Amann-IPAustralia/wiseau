"""Pytest configuration for the backend test suite.

Living at the backend root, this file makes pytest add ``backend/`` to
``sys.path`` so tests can ``import main`` and ``import parsers`` the same way
the application does at runtime, without an editable install.
"""

from __future__ import annotations
