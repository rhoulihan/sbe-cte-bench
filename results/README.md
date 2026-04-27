# results/

Run output lands here. Gitignored except for `processed/` (which holds the published per-scenario writeups and charts).

## Layout

```
results/
├── raw/                            # gitignored
│   ├── S01-warm-2026-04-25T15:32:00Z.json
│   ├── S01-cold-2026-04-25T15:34:00Z.json
│   ├── S03-k4-warm-2026-04-25T15:36:00Z.json
│   └── …                           # one JSON per (scenario, variant, time) tuple
├── processed/
│   ├── summary.csv                 # one row per (scenario, variant); for the headline table
│   ├── claim-coverage.md           # per-claim coverage proof
│   ├── cross-scenario.md           # claim 11 (single iterator tree) cross-scenario summary
│   ├── scenario-S01.md             # per-scenario writeups
│   ├── scenario-S02.md
│   └── …
└── charts/                         # gitignored except .gitkeep
    ├── S03-boundary-tax.svg
    ├── S04-stage-wall.svg
    └── …
```

## Schema for raw run records

Defined in `../docs/07-reporting.md`. JSON, schema_version "1.0".

## Reproducibility

A published result includes:

1. The Git SHA of `sbe-cte-bench` at run time.
2. The Git SHA of the data generator at run time.
3. The data hash from `data/generated/manifest.json`.
4. Container image digests (Mongo + Oracle).
5. Host hardware fingerprint.
6. Knob settings on both engines.
7. The exact per-scenario predictions and their pass/fail outcomes.

A run without this manifest is not reportable.
