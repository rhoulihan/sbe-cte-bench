"""Run record schema — the single output artifact per scenario invocation.

Schema mirrors ``docs/07-reporting.md``. Pydantic models give us validation,
JSON serialization, and a consumer-friendly type for the reporting layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class HostInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kernel: str
    cpu_model: str
    physical_cores: int
    memory_gb: int
    storage: str


class WaitEventEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: str
    waits: int
    time_seconds: float
    pct_db_time: float


class StatspackBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    begin_snap_id: int | None = None
    end_snap_id: int | None = None
    elapsed_minutes: float | None = None
    db_time_minutes: float | None = None
    top_wait_events: list[WaitEventEntry] = Field(default_factory=list)
    load_profile: dict[str, float] = Field(default_factory=dict)
    report_path: str | None = None


class TimingBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timings_ms: list[float]
    median_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    iqr_ms: float
    cv: float
    n: int
    p99_low_confidence: bool
    cpu_user_ms_median: float = 0.0
    peak_rss_mb: int = 0
    csw_voluntary: int = 0
    csw_involuntary: int = 0
    io_read_bytes: int = 0
    io_write_bytes: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)


class MongoBlock(TimingBlock):
    model_config = ConfigDict(extra="forbid")
    version: str
    framework_control: str
    wt_cache_gb: float
    pipeline: list[dict[str, Any]] = Field(default_factory=list)
    explain: dict[str, Any] = Field(default_factory=dict)
    spill: dict[str, Any] = Field(default_factory=dict)


class OracleBlock(TimingBlock):
    model_config = ConfigDict(extra="forbid")
    version: str
    sga_mb: int
    pga_mb: int
    sql: str
    plan: dict[str, Any] = Field(default_factory=dict)
    workarea: dict[str, Any] = Field(default_factory=dict)
    statspack: StatspackBlock = Field(default_factory=StatspackBlock)
    sql_monitor_path: str | None = None
    """Relative path to the active SQL Monitor HTML report saved under
    ``results/sql_monitor/``. ``None`` if capture was skipped or failed."""


class EquivalenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mongo_hash: str
    oracle_hash: str
    match: bool
    row_count_mongo: int
    row_count_oracle: int


class PredictionBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    claim: str
    expected: dict[str, Any]
    observed: dict[str, Any]
    pass_: bool = Field(alias="pass", serialization_alias="pass")


class RunRecord(BaseModel):
    """A complete scenario run record.

    The output unit per (scenario, variant, timestamp) tuple. Persisted as
    one JSON file under ``results/raw/Sxx-<variant>-<timestamp>.json``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    timestamp: datetime
    scenario: str
    scenario_title: str
    variant: dict[str, Any]
    host: HostInfo
    mongo: MongoBlock
    oracle: OracleBlock
    equivalence: EquivalenceBlock
    prediction: PredictionBlock
