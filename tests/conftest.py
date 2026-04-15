"""
Shared pytest configuration.

Sets SIPHON_DEV_MODE=true so that jwt_utils.py startup validation logs a warning
instead of raising RuntimeError when SIPHON_JWT_SECRET is not set in the test env.
"""
import os

os.environ.setdefault("SIPHON_DEV_MODE", "true")
