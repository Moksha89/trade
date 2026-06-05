"""Test environment setup.

Runs before any test module is imported, so `app.config.settings` (instantiated
at import time) picks up these values.
"""

import os

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "secret123")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")  # force heuristic fallback in tests
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_trading.db")
