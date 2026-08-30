# Container Dispatch Intelligence Platform — System Spec

**Purpose of this document:** This is the single source of truth for the project. It's written to be
self-contained — paste it into a new Claude or Gemini chat and either can resume work with zero lost
context. Update the "Progress Log" section at the bottom as phases complete.

**Owner:** Abdul Raffay
**Hard constraint:** $0 cost. No paid tiers, no "free tier of a paid product" that could later
lock/expire/require a card. Everything runs on GitHub (Actions, Pages, Releases) + Streamlit Community Cloud.

---

## 1. What This System Does

Tracks cargo vessel voyages in near-real-time using free AIS (Automatic Identification System) data,
predicts ETA delays with an ML model, flags anomalies (AIS blackouts, congestion), and presents it all
through:
1. A live **Streamlit dashboard** (interactive, CSV export)
2. A **static HTML mirror** on GitHub Pages (no server needed, always up)
3. A human-readable **DISPATCH_LOG.md** audit trail, auto-regenerated

Data refreshes every 4 hours via GitHub Actions. No manual intervention after setup.

**Important framing note:** True container-level (bill-of-lading) tracking is a paid data product
(Terminal49, ShipsGo, JSONCargo). This system tracks **vessel voyages** (origin port → destination port,
ETA, route) using free AIS feeds as a legitimate, honestly-labeled proxy. The README/dashboard should say
this plainly — "vessel voyage tracking," not "container tracking," to avoid overclaiming what free data
can support.

---

## 2. Architecture — Final Decision

**Pattern: Dual-Tier Hybrid, fully GitHub-native (no external cloud accounts of any kind)**

| Layer | Contents | Storage | Committed to git? |
|---|---|---|---|
| **Hot state** | `latest_voyages.parquet`, `predictions.parquet` — pruned, small (rolling window) | Repo root `/data/` | Yes — this is what Streamlit + Pages read live |
| **Audit trail** | `DISPATCH_LOG.md`, static Plotly HTML export | Repo `/docs/` (GitHub Pages source) | Yes — regenerated every run |
| **Cold history** | Full raw AIS snapshot per run (gzipped JSON/Parquet) | **GitHub Release assets**, tagged by date (`snapshot-2026-08-29-0000`) | No — attached to a Release, not git history |

**Why this exact shape:**
- Committing a monolithic `.db` file every 4 hours bloats git (binary diffs don't compress) — avoided by keeping only small pruned Parquet files in git.
- Cold history goes to GitHub Releases instead of S3/Supabase/MotherDuck — zero external accounts, zero risk of a "free tier" policy changing later. GitHub Releases have no meaningful size problem at this data volume.
- Streamlit and the static HTML both read the same `/data/*.parquet` files — no duplicate logic.

---

## 3. Data Source

**Primary: AISstream.io** — free, real-time AIS over WebSocket. No credit card required for signup.

- Alternative/backup: **AISHub** (community feed, JSON/XML/CSV) if AISstream has downtime.
- Scope the bounding box to major shipping lanes first (e.g., Strait of Malacca, Suez approach, US West Coast approach) rather than global — keeps ingestion volume manageable on a free WebSocket connection running inside a time-boxed GitHub Actions job.

**Known data quality issues to handle from day one (not later):**
1. **Dirty destination strings** — AIS destination fields are free-typed by crew (`"US LAX"`, `"ROTTDAM"`, `"FOR ORDERS"`). Fix: fuzzy-match (`thefuzz`, Levenshtein) against a static UN/LOCODE reference table, with a manual `destination_corrections.csv` override checked first for recurring garbage strings.
2. **AIS blackouts** — vessels go dark near contested waters or blind spots. Fix: treat `time_since_last_ping > threshold` as a first-class flag (`ais_blackout = true`), never silently drop the row.
3. **WebSocket in CI/CD** — GitHub Actions runners aren't long-lived processes. Fix: bounded async listener that drains messages for a fixed window (e.g., 90–120 seconds), dedupes by MMSI, closes cleanly, then exits.

---

## 4. Data Schema

Stored as Parquet (hot) + Release-archived raw snapshots (cold). Logical schema:

**`vessels`** (slowly-changing reference data)
```
mmsi (PK) | imo | vessel_name | vessel_type | flag | length | width
```

**`voyages`** (one row per active/completed voyage)
```
voyage_id (PK) | mmsi (FK) | origin_unlocode | origin_port_name
dest_unlocode | dest_port_name | dest_raw_string | dep_time
eta_reported | eta_predicted | status | ais_blackout | last_updated
```

**`port_calls`** (event log — append-only)
```
event_id (PK) | mmsi | unlocode | event_type | event_time | lat | lon
```

**`predictions`** (model output + tracking)
```
voyage_id (FK) | run_timestamp | eta_delay_predicted_hrs | model_version
mae_at_batch | rmse_at_batch
```

---

## 5. ML Plan

**Start with exactly one model. Do not build all three at once.**

### Phase 1 (build this first): ETA Delay Delta
- **Target:** `realized_arrival - reported_eta` (hours), not an absolute timestamp — deltas are more stable to predict than absolute times.
- **Features:** speed over ground, distance-to-destination (great-circle), vessel class, historical route variance for that origin→dest pair.
- **Model:** XGBoost regression (already in your stack).
- **Validation output:** log MAE/RMSE/R² per batch run to the `predictions` table → chart "Model Performance Over Time" on the dashboard. This is the single most convincing artifact for anyone reviewing the project — build this chart early, even with a weak model.

### Phase 2 (after Phase 1 is stable): Port Congestion Risk
- Classifier: is the destination port currently bottlenecked, based on count of vessels idling/anchoring within ~20nm.
- Needs different data shape (spatial clustering) — don't build simultaneously with Phase 1.

### Already free, build immediately, no ML needed: AIS Blackout Flag
- Simple rule (`time_since_last_ping > threshold`). Lives in the ingestion layer, not the ML layer. Solves the "missing data" problem structurally rather than statistically.

---

## 6. GitHub Actions — Job Structure

One workflow, `.github/workflows/dispatch_pipeline.yml`, `cron: '0 */4 * * *'`, three sequential jobs:

1. **`ingest`** — connect to AISstream, bounded-listen, dedupe by MMSI, upsert into `/data/*.parquet`, fuzzy-clean destinations, flag blackouts. Upload raw snapshot as a Release asset.
2. **`predict`** — load updated `voyages.parquet`, run XGBoost model, write `predictions.parquet`, regenerate Plotly chart images/HTML.
3. **`publish`** — regenerate `DISPATCH_LOG.md` from the Parquet data, commit `/data/`, `/docs/` (static HTML + charts) back to the repo.

Keep all three in one workflow file as sequential steps unless ingestion volume grows enough to need independent retry/scheduling.

---

## 7. Dashboards

**Streamlit app** (`app.py`, deployed on Streamlit Community Cloud — free):
- Reads `/data/*.parquet` directly (no DB server, no locking issues)
- Filters: route, vessel, status, blackout flag
- `st.download_button` → raw CSV export of the current filtered view
- Model performance chart (MAE/RMSE over time) as its own tab
- Dark/glassmorphism styling consistent with the genomic-variant-platform project

**Static HTML** (`/docs/index.html`, served via GitHub Pages):
- Pre-rendered Plotly figures exported to HTML divs at publish time (no live Python — Pages is static)
- Exists so the project has a permanent public URL independent of whether Streamlit Cloud is up

---

## 8. Step-by-Step Build Order

Work through these phases in order. Each phase should be a working, committed state before moving to the next — don't parallelize phases across chats/collaborators, since half-built phases are the "hurdles" this doc is meant to avoid.

1. **Repo scaffold** — folder structure (`/data`, `/docs`, `/scripts`, `/models`, `.github/workflows/`), README stating the "vessel voyage tracking, not container/BoL tracking" framing.
2. **UN/LOCODE reference table** — static CSV, load once, this is a dependency for everything downstream.
3. **Ingestion script** — AISstream WebSocket, bounded listener, writes raw snapshot locally. Test this standalone (run it manually) before wiring into Actions.
4. **Destination cleaning pipeline** — fuzzy matcher + manual overrides CSV, tested against real captured AIS strings from step 3.
5. **Parquet upsert logic** — raw snapshot → `vessels`/`voyages`/`port_calls` Parquet files, with blackout flagging.
6. **GitHub Actions wiring** — `ingest` job only, first. Confirm it runs on schedule and commits correctly before adding `predict`/`publish`.
7. **DISPATCH_LOG.md generator** — script that reads Parquet, writes the human-readable log. Add as `publish` job.
8. **Streamlit MVP** — read the committed Parquet, basic table + filters + CSV download. Deploy to Streamlit Cloud.
9. **ETA Delay model v1** — once you have enough historical voyages with realized arrivals (needs a few days/weeks of accumulated data — this phase has a natural waiting period, use it to work on the dashboard polish instead).
10. **Model performance chart + `predict` Actions job**.
11. **Static HTML export + GitHub Pages `publish` step**.
12. **Phase 2 additions** — port congestion scoring, historical replay slider, port congestion leaderboard (see backlog below).

---

## 9. Backlog / Unique Additions (post-MVP)

- Historical replay slider on the dashboard (scrub through past 4-hour snapshots, not just "now")
- Port congestion leaderboard — aggregate delay-risk ranked by port
- AIS blackout as a visible map layer, not just a table flag

---

## 10. Progress Log

*(Update this section as work happens — this is what lets a new chat resume with no lost context.)*

- [x] Repo scaffold
- [ ] UN/LOCODE reference table
- [ ] Ingestion script
- [ ] Destination cleaning pipeline
- [ ] Parquet upsert logic
- [ ] GitHub Actions — ingest job
- [ ] DISPATCH_LOG.md generator
- [ ] Streamlit MVP deployed
- [ ] ETA Delay model v1
- [ ] Model performance chart + predict job
- [ ] Static HTML + GitHub Pages
- [ ] Phase 2 backlog items