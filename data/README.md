# data/

Generated benchmark data lands here. Gitignored — the data is reproducible from `harness/data/generator.py` given a fixed seed.

## Expected contents (after generation)

```
data/
├── generated/
│   ├── customers.bson          # BSON for mongorestore
│   ├── customers.csv           # CSV for SQL*Loader
│   ├── products.bson
│   ├── products.csv
│   ├── categories.bson
│   ├── categories.csv
│   ├── regions.bson
│   ├── regions.csv
│   ├── suppliers.bson
│   ├── suppliers.csv
│   ├── orders.bson
│   ├── orders_doc.json         # JSON files containing the OSON payload column
│   ├── orders_rel.csv          # Relational row data for orders_rel
│   ├── order_line_items_rel.csv
│   └── manifest.json           # Hashes, seed, scale_factor, generator git sha
├── extensions/                 # Optional, for S04/S05
│   ├── S04_deep_skew.bson
│   ├── S04_deep_skew.csv
│   ├── S05_hot_customers.bson
│   └── S05_hot_customers.csv
└── README.md                   # this file (committed)
```

## Determinism

Two invocations of `harness/data/generator.py` with the same seed and scale factor must produce byte-identical output. A `manifest.json` records the SHA-256 of every file; if your local generator output's hash doesn't match a published manifest, fix the generator before benchmarking.

## Disk requirements

| SF | Disk used (raw) | Disk used (with indexes after load) |
|----|-----------------|-------------------------------------|
| SF0.1 | ~3 GB | ~5 GB per engine |
| SF1 | ~30 GB | ~50 GB per engine |
| SF10 | ~300 GB | ~500 GB per engine |

Total disk for SF1 baseline = ~30 GB raw + 2 × 50 GB loaded ≈ 130 GB. Reference hardware (1 TB NVMe) handles this comfortably.
