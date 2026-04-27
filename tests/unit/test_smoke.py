"""Smoke test: package imports and exposes a version string."""

from __future__ import annotations

import re

import pytest

import sbe_cte_bench


@pytest.mark.unit
def test_version_is_pep440_compatible() -> None:
    """Package version must be PEP 440 compliant so build tooling can parse it."""
    pep440 = re.compile(r"^\d+\.\d+\.\d+(\.dev\d+|\.\w+\d*)?$")
    assert pep440.match(sbe_cte_bench.__version__), (
        f"version {sbe_cte_bench.__version__!r} is not PEP 440 compatible"
    )


@pytest.mark.unit
def test_version_is_nonempty() -> None:
    assert sbe_cte_bench.__version__
    assert isinstance(sbe_cte_bench.__version__, str)
