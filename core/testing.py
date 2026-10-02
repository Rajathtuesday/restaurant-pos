"""Helpers for tests across the apps."""
from types import SimpleNamespace
from unittest.mock import patch


def freeze_ratelimit_clock(test):
    """django_ratelimit counts in fixed one-minute windows. Twenty real
    requests on a busy machine can take long enough to cross into the next
    window, which starts the count again and lets the next request through
    (seen in a full-suite run on 1 Oct 2026). Its clock, and only its clock,
    is held still for the test."""
    patcher = patch("django_ratelimit.core.time", SimpleNamespace(time=lambda: 1_800_000_000))
    patcher.start()
    test.addCleanup(patcher.stop)
