"""Top-level pytest configuration.

Hypothesis profile registration runs at import time so every test session sees
the same defaults regardless of test invocation order.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

settings.register_profile(
    "default",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
settings.register_profile(
    "ci",
    parent=settings.get_profile("default"),
    max_examples=50,
)
settings.register_profile(
    "thorough",
    parent=settings.get_profile("default"),
    max_examples=1000,
)

_active_profile = os.environ.get("HYPOTHESIS_PROFILE", "ci" if os.environ.get("CI") else "default")
settings.load_profile(_active_profile)
