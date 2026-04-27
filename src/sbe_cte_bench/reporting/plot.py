"""Chart generation.

All charts are SVG, 800x500 px, with a fixed colour palette and pinned
matplotlib version (in ``pyproject.toml``) plus ``svg.hashsalt`` so output is
byte-stable across runs. Stable SVGs let golden-file tests catch unintended
rendering changes (matplotlib upgrades, font metric drift) at PR time.

Colours per ``docs/07-reporting.md``:

- MongoDB: ``#0e6c3f`` (dark green)
- Oracle: ``#9d2235`` (dark red)
- Background: ``#fafafa``
- Grid lines: ``#dddddd`` dashed
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend; no display required.
import matplotlib.pyplot as plt

MONGO_COLOR = "#0e6c3f"
ORACLE_COLOR = "#9d2235"
BG_COLOR = "#fafafa"
GRID_COLOR = "#dddddd"
SVG_HASH_SALT = "sbe-cte-bench-v1"


def set_deterministic_rcparams() -> None:
    """Pin matplotlib parameters for byte-stable SVG output."""
    plt.rcParams["svg.hashsalt"] = SVG_HASH_SALT
    plt.rcParams["svg.fonttype"] = "none"
    plt.rcParams["axes.facecolor"] = BG_COLOR
    plt.rcParams["figure.facecolor"] = "white"
    plt.rcParams["font.size"] = 10
    plt.rcParams["axes.labelsize"] = 12
    plt.rcParams["axes.titlesize"] = 13


def latency_vs_variant_chart(
    *,
    title: str,
    variant_axis: str,
    variant_labels: Sequence[str],
    mongo_medians: Sequence[float],
    oracle_medians: Sequence[float],
    output: Path | str,
) -> None:
    """Render the standard "latency vs swept knob" bar chart.

    Used by S03 (boundary position), S04 (working set), S08 (window size),
    S13 (data scale). X-axis is the swept parameter; Y-axis is median latency.
    Two series — Mongo and Oracle.
    """
    if not variant_labels:
        raise ValueError("empty variant_labels")
    if len(variant_labels) != len(mongo_medians) or len(variant_labels) != len(oracle_medians):
        raise ValueError("variant_labels, mongo_medians, oracle_medians must have equal length")

    set_deterministic_rcparams()
    fig, ax = plt.subplots(figsize=(8, 5))

    width = 0.4
    indices = list(range(len(variant_labels)))
    mongo_bars = [i - width / 2 for i in indices]
    oracle_bars = [i + width / 2 for i in indices]

    ax.bar(mongo_bars, mongo_medians, width=width, color=MONGO_COLOR, label="MongoDB 8.x")
    ax.bar(oracle_bars, oracle_medians, width=width, color=ORACLE_COLOR, label="Oracle 26ai Free")

    ax.set_xticks(indices)
    ax.set_xticklabels(list(variant_labels))
    ax.set_xlabel(variant_axis)
    ax.set_ylabel("Median latency (ms)")
    ax.set_title(title)
    ax.legend(frameon=False)
    ax.grid(True, axis="y", color=GRID_COLOR, linestyle="--", linewidth=0.5)
    ax.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(str(output), format="svg")
    plt.close(fig)
