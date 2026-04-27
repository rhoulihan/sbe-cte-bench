"""Parse MongoDB ``system.profile`` entries for spill metrics.

MongoDB 8.1+ standardized per-stage spill counters with names like
``groupSpills``, ``groupSpilledBytes``, ``groupSpilledRecords``,
``groupSpilledDataStorageSize``. The legacy 8.0 / 7.x form was a single
``usedDisk: true`` boolean. This parser handles both.

The blocking operators that can spill are:

- ``$group``        → ``group*`` counters
- ``$sort``         → ``sort*`` counters
- ``$bucket``       → ``bucket*`` counters
- ``$bucketAuto``   → ``bucketAuto*`` counters
- ``$setWindowFields`` → ``setWindowFields*`` counters

Each scenario's run record references this output via
``mongo.spill.<stage>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Stage names whose spill metrics we extract. Names match the field-prefix
# convention MongoDB uses in 8.1+ system.profile.
_SPILL_STAGES = ("group", "sort", "bucket", "bucketAuto", "setWindowFields")


@dataclass(frozen=True)
class StageSpill:
    spill_count: int = 0
    spilled_bytes: int = 0
    spilled_records: int = 0
    spilled_data_storage_size: int = 0


@dataclass(frozen=True)
class SpillMetrics:
    has_spill: bool
    per_stage: dict[str, StageSpill] = field(default_factory=dict)
    legacy_used_disk: bool = False

    def total_spilled_bytes(self) -> int:
        return sum(s.spilled_bytes for s in self.per_stage.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_spill": self.has_spill,
            "legacy_used_disk": self.legacy_used_disk,
            "per_stage": {
                stage: {
                    "spill_count": s.spill_count,
                    "spilled_bytes": s.spilled_bytes,
                    "spilled_records": s.spilled_records,
                    "spilled_data_storage_size": s.spilled_data_storage_size,
                }
                for stage, s in self.per_stage.items()
            },
        }


def parse_profile_entry(entry: dict[str, Any]) -> SpillMetrics:
    """Extract spill metrics from a single ``system.profile`` document."""
    per_stage: dict[str, StageSpill] = {}
    for stage in _SPILL_STAGES:
        spill_count = entry.get(f"{stage}Spills")
        spilled_bytes = entry.get(f"{stage}SpilledBytes")
        spilled_records = entry.get(f"{stage}SpilledRecords")
        spilled_storage = entry.get(f"{stage}SpilledDataStorageSize")

        if any(
            v is not None for v in (spill_count, spilled_bytes, spilled_records, spilled_storage)
        ):
            per_stage[stage] = StageSpill(
                spill_count=int(spill_count or 0),
                spilled_bytes=int(spilled_bytes or 0),
                spilled_records=int(spilled_records or 0),
                spilled_data_storage_size=int(spilled_storage or 0),
            )

    legacy = bool(entry.get("usedDisk", False))
    has_spill = bool(per_stage) or legacy

    return SpillMetrics(has_spill=has_spill, per_stage=per_stage, legacy_used_disk=legacy)
