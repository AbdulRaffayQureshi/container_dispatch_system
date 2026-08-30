# Container Dispatch Intelligence Platform

**⚠️ Framing note — read this first:** This project tracks **vessel voyages** (origin port →
destination port, ETA, route) using free, public AIS (Automatic Identification System) data. It does
**not** do container-level / bill-of-lading tracking. True container tracking is a paid data product
(Terminal49, ShipsGo, JSONCargo) that this project intentionally does not attempt to replicate.
Everywhere in this repo, dashboards, and docs, we say "vessel voyage tracking" — never "container
tracking" — to avoid overclaiming what free AIS data can support.

## What it does

- Ingests near-real-time AIS data for vessels in major shipping lanes
- Predicts ETA delays with an ML model (XGBoost, starting with delay-delta regression)
- Flags anomalies: AIS blackouts, destination-string data-quality issues
- Publishes the result three ways:
  1. A live **Streamlit dashboard** (interactive, CSV export)
  2. A **static HTML mirror** on GitHub Pages (always up, no server required)
  3. A human-readable `DISPATCH_LOG.md` audit trail, auto-regenerated every run

Data refreshes every 4 hours via GitHub Actions. No manual intervention after setup.

## Hard constraint

**$0 cost.** No paid tiers, no "free tier of a paid product" that could later lock, expire, or require
a card. Everything runs on GitHub (Actions, Pages, Releases) + Streamlit Community Cloud.

## Architecture (dual-tier hybrid, fully GitHub-native)

| Layer | Contents | Storage | Committed to git? |
|---|---|---|---|
| Hot state | `latest_voyages.parquet`, `predictions.parquet` (pruned, rolling window) | `/data/` | Yes |
| Audit trail | `DISPATCH_LOG.md`, static Plotly HTML export | `/docs/` (GitHub Pages source) | Yes |
| Cold history | Full raw AIS snapshot per run (gzipped) | GitHub Release assets, tagged by date | No |

See `CONTAINER_DISPATCH_SYSTEM_SPEC.md` in the repo root for the full system spec — data source,
schema, ML plan, Actions job structure, and the complete step-by-step build order. That document is
the single source of truth for this project; update its Progress Log as phases complete.

## Repo layout

```
.
├── data/                     # hot Parquet state, committed by the publish job
├── docs/                     # GitHub Pages source — static HTML mirror
├── scripts/                  # ingestion, cleaning, upsert, log-generation scripts
├── models/                   # trained model artifacts (XGBoost)
├── .github/workflows/        # dispatch_pipeline.yml — ingest → predict → publish
├── CONTAINER_DISPATCH_SYSTEM_SPEC.md
└── README.md
```

## Status

Repo scaffold — build in progress. See the Progress Log in the system spec for current phase.