"""
communications/utils.py

Per the spec's `existing_modules.announcements.allowed` list — "Read helper
methods" / "Reuse utility methods" — we import (never edit) two small pure
functions from announcements/utils.py instead of re-implementing the same
HTML-stripping/sanitizing logic a third time in the codebase. Nothing here
writes to announcements' tables or calls its views/api.
"""

from announcements.utils import sanitize_rich_text, build_push_body  # noqa: F401 — read-only reuse

__all__ = ['sanitize_rich_text', 'build_push_body']
