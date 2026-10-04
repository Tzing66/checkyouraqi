# Decision log

Decisions not covered by the plan, newest first. Format: date — decision — why.

## 2026-10-01 — Phase 5 decisions (owner) and prep
- **OpenAQ status 2026-10-01:** OpenAQ backfilled the 24–29 Sep CPCB gap (Anand Vihar 21–24 h/day), then **stalled again from 2026-09-29 ~14:30 UTC**. That's two stalls in a week. Our pipeline was paused, so the backfilled hours need a `scripts/backfill.py openaq` rerun (the current month is always re-fetched).
- **Phase 5 choices:**
  - Server: **t4g.small** (ARM, 2 GB, ~USD 18/month incl. disk + public IPv4), the cheapest that fits once training is moved off it.
  - **Weekly training on GitHub Actions** (free runners for public repos, OIDC role limited to `main`), not an Airflow train DAG.
  - `public/` readable by a scoped bucket policy.
  - No server access: no SSH and no open ports; bootstrapped by user-data; SSM kept only as an emergency door.
  - Outage handling: deploy now on OpenAQ alone. A CPCB backup was tried and dropped (below).
- **Budget:** the owner created `checkyouraqi-monthly`: USD 25/month on gross cost (credits excluded), alerts at 50% and 80% actual and 100% forecast. This replaces the plan's USD 1 budget, which would alert constantly once EC2 runs.
- **CPCB backup: tried and dropped (2026-10-04), OpenAQ is the only live source.** During the OpenAQ stall only CPCB's own site (`airquality.cpcb.gov.in`) stayed current, but it **blocks data-centre traffic**: requests time out from AWS Mumbai (EC2) and from GitHub Actions (Azure, US), while home connections work. The alternatives failed too: **WAQI** was stale in step with OpenAQ (23 of 24 NCR stations 1–3 days old, so it shares the broken upstream and is no backup), **data.gov.in**'s API refused connections, and **`app.cpcbccr.com`** now looks like an ad-supported lookalike, not CPCB (Vercel + AdSense, domain record changed 2026-08-31), so it is untrusted. The owner chose to run on OpenAQ only; the dashboard already labels outages. Revisit only if OpenAQ can't deliver live data consistently; the one working route would be a relay on a home connection. The capture code, its two raw snapshots and the unused WAQI key were removed.
- **S3 cost finding:** S3 cost so far is USD 0.22 vs Athena USD 0.02. That's S3 *requests* from Athena/dbt (LIST on hundreds of daily partitions per full-rebuild model, plus writes), not storage. Run hourly unchanged it would be ~USD 12–15/month. **It gets fixed before deployment:** models run only as often as their data changes (hourly/daily/weekly), and full rebuilds become incremental. Measured with S3 server access logs: a separate `checkyouraqi-logs-*` bucket, 7-day expiry, free delivery.
- **dbt cadence (S3 request fix), implemented:**
  - Explicit per-model tags + `selectors.yml`: **hourly** (11 models: live OpenAQ, forecasts, grid, AQI, aggregates, status, live features; `dbt run`, no tests), **daily** (12 models + seeds: ERA5, previous runs, CAMS history, fires, dims; `dbt build` then the **full test suite**; new `dbt_daily` DAG 04:15 UTC), **weekly** (`fct_features_hourly`, built by the training workflow), **predict** (forecast tables).
  - dbt tags are additive, so folder-wide tags are banned, and a test enforces exactly one cadence tag per model.
  - ERA5 actuals, previous runs and CAMS history are now incremental MERGE reading only the last 14 days of bronze (~14 partition listings instead of ~640). Fires is an incremental Iceberg table (10 days), with the SP-over-NRT preference moved to `int_fire_points_located` so later SP files still win.
  - The fire sector grid extends 26h past the build so hourly live features always find a row (no leakage: only already-known fires).
  - After manual backfills of older periods, run these models with `--full-refresh`.
  - The one-off view→table conversion of fires hit a dbt-athena "Query type not supported by DDL engine" on `--full-refresh`. The view was already dropped, and the next run created the table cleanly (158,702 rows).
- **Weekly training on GitHub Actions** (`.github/workflows/train.yml`): Sunday 02:00 IST plus a manual run button.
  - OIDC role `checkyouraqi-github-train` (main branch of this repo only).
  - Steps: dbt `--selector weekly` (training table + leakage test) → `ml.train --refresh` → `ml.promote`.
  - The MLflow SQLite DB persists in `s3://…/mlflow/tracking/`.
  - It skips until the repo variables `AWS_TRAIN_ROLE_ARN` and `DATA_BUCKET` are set.
- **Promotion rule made fair (`ml/promote.py`):**
  - A challenger is trained on data before a holdout and scored against production on that holdout.
  - The holdout is the last 14 days **but never before production's training cutoff**, so production is scored out-of-sample too. Training now records `trained_until_utc`; older models use their promotion time as a conservative cutoff.
  - Fewer than 3 days of new data → "no decision, keep production".
  - First real dry run: an unconstrained 14-day holdout showed production 13.4 vs challenger 22.0 MAE, purely because production had trained on those days. With the fix: "no decision: 0.0 days of data after production's cutoff" (OpenAQ outage).
- **dbt profile no longer pins an AWS profile** (it defaulted to `checkyouraqi-dev`, which doesn't exist on EC2 or GitHub). Credentials follow boto3's default chain: `AWS_PROFILE` from `.env` locally, the instance role on EC2, OIDC on GitHub.
- **Account linkage audit (owner request):** everything points at account 242254325008 only. No trace of the old account; the only local profiles are `checkyouraqi` (tanz-admin) and `checkyouraqi-dev`, both in that account; no default profile; Terraform state in `checkyouraqi-tfstate-242254325008`; pipeline code takes its profile only from `.env` (or the EC2/OIDC role). GitHub `Tzing66/checkyouraqi`.
- **Plan 1 applied** (free): GitHub OIDC provider + `checkyouraqi-github-train` role, SSM emergency-door policy on the EC2 role, security group (no inbound), and a `public/*` read policy. Verified: `public/manifest.json` 200; bronze objects and bucket listing 403. The dashboard loads over HTTPS once it uses a pooled client with retries (parallel per-file TLS connections were being reset).
- **Housekeeping review (2026-10-01):**
  - No orphaned S3 table folders; the Glue catalog matches the dbt project exactly; `public/` keeps 2 runs per dataset; Athena results expire after 7 days.
  - **Iceberg upkeep added:** hourly MERGEs pile up small files and snapshots (some tables at ~20 metadata files after ~10 runs). `run-operation iceberg_maintenance` (daily in `dbt_daily`, independent of tests) sets 1-day snapshot retention (default 5) → OPTIMIZE BIN_PACK → VACUUM. Object counts rise right after the first run (old files are still referenced) and fall as snapshots expire.
  - Removed the Phase 0 spike script (replaced by discovery). Tidied `.gitignore` (duplicates, junk `.bak` lines, obsolete `mlruns/`; plans `*.tfplan` ignored). Updated stale t3.small references and pre-commit-hooks (v4.6 → v6). Added operating notes to `CLAUDE.md`. Removed local experiment leftovers.
  - `ml/` exports get a 14-day S3 expiry (Terraform written, awaiting owner OK; free).
  - Kept deliberately: `alerts/` and `assistant/` (Phase 6), `--target log_ratio` (documented experiment), the AQI breakpoint TODO (verify CPCB's severe upper bound before using sub-index values).
- **Training workflow verified on GitHub** (manual dry run):
  - OIDC → dbt weekly (PASS=5) → train (24/48/72h MAE 31.41/33.80/35.05; Linux float differences vs macOS 31.38/33.84/35.19) → promote: "no decision" (no data after production's cutoff). 6.8 min; **peak 1.4 GB** (confirms keeping training off the 2 GB server).
  - Two fixes:
    1. This repo uses GitHub's **immutable OIDC subject** (`repo:Tzing66@164254504/checkyouraqi@1392845505:ref:refs/heads/main`). The trust policy now matches it exactly, which is safer than the name-based form (a re-created repo with the same name can't assume the role).
    2. Public run logs echoed the bucket name and role ARN (account id). Both are now **GitHub secrets** (masked), and the job guard is the variable `TRAINING_ENABLED`. The two runs whose logs showed the id were deleted.
  - Note: once the dashboard is hosted, the public data URL (`checkyouraqi-<account>.s3...`) inevitably reveals the account id in browsers' network requests. That's inherent to direct public S3 reads (decision 3a). AWS doesn't treat account ids as secret, but a CloudFront domain could hide it later if wanted.
- **Applied (free):** 14-day expiry on `ml/` exports. Repo secrets `DATA_BUCKET` and `AWS_TRAIN_ROLE_ARN` plus variable `TRAINING_ENABLED=true`. Backup branch `backup/pre-author-rewrite` deleted (owner OK).
- **S3 request measurement (access logs) — before/after:**
  - Old hourly build: **21,041 requests, USD 0.058/run → ~USD 42/month** (3× the original estimate). Top Tier-1 sources: previous runs 3,107, CAMS history 1,256, ERA5 actuals 1,255 listings (full ~640-day partition scans).
  - Cadence + incremental readers: hourly **3,631 (USD 0.0099)**, daily **3,743 (USD 0.0058)** → **~USD 7.3/month (−83%)**.
  - Remaining hourly cost was day-level filters on hourly bronze tables (~72 folders listed per table per run). PM2.5 was worst because its cutoff used reading time, which during the outage stayed on 29 Sep, so it listed 100+ folders.
- **Hour-level partition filters:** `recent_hourly_partitions()` filters on (dt, hour) since the latest *fetch* stored in the table minus 6h. It self-heals after downtime (reaches back to the last successful run). The PM2.5 model has a 24h cap because its fetch time can't advance during a source outage (empty files); **downtime longer than 24h → rerun `scripts/backfill.py openaq`**. Freshness checks scan only the last 6h of snapshots (`hours_ago_partition_filter`), which is correct because each hourly snapshot carries every sensor's newest reading. Measured 2026-10-01 14:33:12–14:35:09 UTC: hourly **2,178 requests (Tier-1 675), USD 0.0040/run** → with the daily run **~USD 3.0/month (−93% vs the old USD 42)**. The remaining Tier-1 traffic is ~30 listings per hourly table, spread evenly; no single hotspot.
- **Measurement method:** windows 11:54:15–11:57:13 (old), 12:00:57–12:03:04 (cadence), 12:06:21–12:08:54 (steady-state daily), 14:33:12–14:35:10 (hour filters), all UTC 2026-10-01. S3 access logs arrive out of order over ~1.5h, so "latest timestamp seen" is not a completeness check: wait until a window's line count stops changing (the first cadence figures, USD 6.1/month, were from incomplete logs).

## 2026-09-29 — Dashboard (Phase 4)
- **Streamlit + folium map + Altair charts, reading only `public/`** (manifest → Parquet, fetched in parallel; the manifest now lists each dataset's files and an `empty` flag, so it also works over plain HTTPS in Phase 5). Cached for 5 minutes.
- **Pages:** Overview (city/zone tiles, station map, nearest-station lookup via locality search / coordinates / map click), Station (status, last reading, 24/48/72h forecast cards with a stale-input warning, 30-day history, forecast vs actual), Model health (backtest vs baselines, MAE by month, top features, live accuracy, drift), About (attribution, method, caveats).
- **AQI colours:** India's official hues, treated as a fixed status scale (like the dataviz skill's status palette: never themed, always shipped with the category name as text). The dataviz validator shows the official scale fails the one-hue ordinal rules (by design, it's multi-hue), so labels are mandatory. Two swatches are darkened to clear the 2:1 floor on light and dark surfaces: satisfactory `#92D050` → `#7FBF3F`, moderately polluted yellow → `#C9A800`. Two-series charts use dataviz categorical slots 1–2 (blue/orange; dark-mode steps too), with legend and direct labels.
- **Nominatim** (locality search): identifying User-Agent, ≤ 1 req/s, results cached a day, bounded to the NCR box, OSM attribution shown.
- **Drift verdict needs ≥ 24 served hours** (see Phase 4 monitor).
- **Performance** (AppTest, live S3 snapshot): cold Overview load 1.95 s after parallelising fetches (was 2.9 s sequential), warm 0.07–0.4 s on every page; criterion < 3 s. No exceptions on any page.
- **Lint:** E501 is relaxed for `tests/**` only (fixture data).
- **Visual review (rendered with headless Chrome, per the dataviz skill's "render and look" step) found and fixed:**
  - CARTO basemaps now return an "API KEY REQUIRED" watermark tile → switched to OpenStreetMap standard tiles (free with attribution).
  - Chart text defaulted to black (invisible on the dark theme) → text uses the palette's text tokens per theme.
  - Legends overlapped data → moved above the plot.
  - End labels collided → nudged apart when series end close together.
  - AQI boundary labels were illegible in-plot → the y-axis ticks now *are* the boundaries ("60 · Satisfactory"). Layers must share one y-encoding object, because Vega-Lite merges per-layer axes and a differing one mangles or removes the axis.
  - Truncated feature names → wider labels.
  - "None" in the skill column → "—".
  - Forecast cards show the hour window ("15:00–16:00 IST").
- **Serving in Airflow:**
  - The image adds `libgomp1` (LightGBM's OpenMP runtime) and `lightgbm` / `evidently`. `ml/` is mounted read-only, and the model cache goes to `/tmp` (`CHECKYOURAQI_MODEL_CACHE`).
  - predict and monitor DAGs pass. Measured scheduler peaks: `dags test` predict 899/900 MiB (worst case: all tasks in one process); a **real triggered run 760/900 MiB** (separate task processes); monitor 682 MiB.
  - The scheduler cap is the constraint to revisit on the t3.small in Phase 5.
- **API (FastAPI):** `/health`, `/stations`, `/stations/nearest`, `/forecast/{id}`. Every station and forecast carries freshness, and forecasts carry `is_stale_input` / `input_age_hours`. Warm responses < 30 ms; first call ~0.8 s (snapshot load).


## 2026-09-29 — Phase 4 decisions (owner)
1. **Forecast even when station data is stale (option b):** forecasts are produced from each station's last known values and labelled with the input age (`input_age_hours`, `is_stale_input`). Surfaces show a warning instead of hiding the forecast.
2. **Live weather features = the same previous-runs forecasts as training** (`days_before = h/24 + 1`, fetched live from Open-Meteo's previous-runs API), so train and serve inputs match.
3. **Live features come from the same dbt logic as training** (a "latest hour, every station" slice). If recent PM2.5 is missing, the lag/rolling/last-value features **carry the last known readings forward** (LOCF). Training keeps gaps as missing, which is what the model learned.
4. **Production model = `s3://<bucket>/models/production/`** with a manifest, promoted manually for now. The automatic promotion rule comes in Phase 5.
5. **Dashboard pages:** Overview (map + zone/city + outage banner), Station (72h forecast, history, forecast vs actual), Model health (backtest, baselines, drift), About (attribution, method, caveats). The owner may revise.
6. **Nearest-station lookup:** both a map click / coordinates and a locality search via OpenStreetMap Nominatim (rate-limited, attribution required).
7. **Accuracy:** show the Phase 3 backtest as historical accuracy now. Live forecast-vs-actual switches on automatically once PM2.5 flows again.
8. **Drift:** a daily Evidently static HTML report in `reports/`, linked from the Model health page.

## 2026-09-29 — Phase 3 modelling
- **Setup:**
  - Prediction ("issue") time = end of an OpenAQ hour. Target = hourly PM2.5 in the OpenAQ hour starting `h` after the issue hour (h = 24/48/72).
  - One global LightGBM per horizon (station and zone as categorical features), L1 objective, fixed seed and `deterministic=True`.
  - Training rows are sampled every 3h per station: 647k rows with a valid target.
- **Leakage (plan §7), enforced in two places:**
  1. `fct_features_hourly` only joins data available at issue time. Its `audit_*` columns record each feature group's latest input time, and the dbt test `assert_features_no_leakage` fails on any later than issue time. The audits are 100% populated (so the test isn't vacuous). Minimum margins: weather forecasts 18.5h, CAMS 12.5h, fires and observed weather 0.5h.
  2. Python tests check the model's feature list never includes the target, audit or timing columns, and that CV folds purge training rows whose labels cross into the test month.
- **Weather-forecast rule:** horizon h uses the Open-Meteo previous run with `days_before = h/24 + 1`. Open-Meteo bins runs by lead-time day, and runs publish a few hours after init, so `days_before = h/24` could include forecasts not yet available at issue time. Needed `previous_day4` for 72h (added and backfilled).
- **Baselines:**
  - Persistence: last valid value.
  - Seasonal naive: mean of the same hour over the previous 7 days. With whole-day horizons, "same hour yesterday" is identical to persistence.
  - CAMS lagged 12h. The true CAMS-*forecast* baseline can't be scored historically and starts from live collection.
- **Validation:** walk-forward monthly folds (expanding window, ≥ 6 months of training, 14 test months 2025-08 → 2026-09). Metrics are pooled over the test months.
- **Results (`docs/model_results.md`, one command `python -m ml.train`, bit-for-bit reproducible):**
  - 24h MAE 31.38 vs persistence 33.65 (**+6.7%**; the acceptance criterion is met) and vs seasonal naive 33.18 (+5.4%).
  - 48h 33.84: +12.0% vs persistence, +0.7% vs seasonal naive.
  - 72h 35.19: +12.0% vs persistence, **−1.0% vs seasonal naive**.
  - Peak memory ~0.75–1.05 GB (< 1.5 GB budget). ~5 min on the Mac.
- **Honest caveats:**
  - The model loses to persistence in **Oct–Nov 2025**, the first pollution season. Its training data (Feb–Sep 2025) has never seen a season onset. From Dec 2025 it beats persistence every month (e.g. Feb–Sep 2026 by 10–25%).
  - Bias is −15 to −18 µg/m³ (L1 → median, under-predicts peaks).
  - Category accuracy is about the same as the baselines (~44–49%, indicative on hourly values).
  - At 48–72h the model is only on par with seasonal naive.
  - More seasons (Oct 2026 arrives via live ingestion) is the biggest expected improvement.
- **Tried and rejected: `--target log_ratio`** (model log1p(target) − log1p(24h mean)) to let trees extrapolate to unseen levels. Overall it was slightly worse (24h MAE 31.76) and only trimmed the Oct–Nov errors a little, which shows the onset is driven by conditions the model hasn't seen, not by value-range extrapolation. It's kept as a CLI option. The model metadata records the transform so prediction decodes correctly.
- **Deferred ideas (not done, to avoid tuning to one season):** blending with seasonal naive at 48–72h, quantile/Huber objectives for the bias, a separate season-regime feature, hyperparameter search.
- **MLflow:** SQLite tracking at `data/ml/mlflow.db` (git-ignored, local), artifacts in `s3://<bucket>/mlflow/pm25-forecast/`. All three runs (initial, log_ratio, final) are recorded. Final models are also saved at `data/ml/models/lgbm_h{24,48,72}.txt` with a `.json` sidecar (transform, feature list).
- **macOS:** LightGBM needs Homebrew `libomp` (installed).

## 2026-09-29 — Phase 2 lakehouse
- **Layout:**
  - Bronze = 9 Athena external tables over the raw files (`aqi_bronze`), created by `dbt run-operation create_bronze_tables`, using the OpenX JSON SerDe and partition projection (no crawlers, no MSCK).
  - Silver (`aqi_silver`) and gold (`aqi_gold`) are dbt models, Iceberg by default, stored at `s3://<bucket>/lake/<schema>/<table>-<uuid>/` (`schema_table_unique`, required for Iceberg table swaps). Plan §6 said `silver/`/`gold/` prefixes; this is equivalent.
- **Incremental only where data grows:** the OpenAQ PM2.5 hourly data, latest readings, and weather/CAMS forecasts use Iceberg MERGE. Weather actuals, previous runs and CAMS history (a few MB each) are rebuilt in full each run, which is simpler and cheap. FIRMS is a view (~13 MB). Plan §3 says "dbt models are incremental"; the full-rebuild ones each scan under 10 MB.
- **Partition pruning:** `incremental_since()` resolves the bronze partition filter to a literal at compile time. A scalar subquery would defeat partition projection and scan everything.
- **Station grid (`int_station_hourly`):**
  - Complete IST-aligned hours from first reading to now. Gaps are flagged (missing, out of range, < 50% coverage, 6h flatline), not filled.
  - `pm25` is set only when valid.
  - History up to the outage: 82% valid, 14.6% missing. Weakest stations: Faridabad Sector 30 (24% valid) and Teri Gram (38%, 201 flatlined hours).
- **AQI:**
  - CPCB 24h average needs ≥ 16 valid hours.
  - The category comes from the rounded 24h average, because CPCB breakpoints are integers (0–30, 31–60...).
  - The sub-index is NULL in the open-ended "severe" band until the upper breakpoint is verified.
  - Zone/city values = median across stations, excluding co-located duplicates.
  - Daily city values use the IST civil day (column `date_ist`); everything else is UTC.
- **Fires:**
  - One satellite per day (SNPP, else NOAA-20). Low-confidence detections dropped.
  - Placed by distance/bearing from Connaught Place into 8 sectors × 3 distance bands.
  - Stubble season 2025 (15 Oct–15 Nov): 6,643 NW detections vs 737 W, which validates the upwind-sector feature.
- **Freshness:**
  - `dbt source freshness` uses `loaded_at_query`. OpenAQ is measured on actual sampling time (3h warn / 6h error, plan §6). Forecasts 2h/6h. FIRMS 2d/4d.
  - It's a separate Airflow task from the build and export, so a stale source goes red without stopping publication of last-known data.
  - `fct_station_status` / `fct_feed_status` implement the owner's stale-data rule. On 2026-09-29, all 70 stations are `inactive`, and the feed flipped to `outage` at 2026-09-24 20:30 UTC (3h after the last CPCB reading).
- **Public export:**
  - Athena UNLOAD → Parquet under `public/<dataset>/run=<id>/`, with `public/manifest.json` written last (readers never see a partial export), keeping the previous run.
  - Timestamps are cast to millisecond precision because UNLOAD's Parquet writer rejects `timestamp(6)`.
  - "Recent" windows are anchored to the newest data, not the clock, so an outage still shows the last 30 days.
  - 10 datasets, ~370 KB, ~2.5 MB scanned per export. Verified reading via DuckDB (httpfs + credential chain).
- **`dbt_build` DAG:** hourly at :25, after ingestion at :05/:10. dbt build → (source freshness ‖ export_public).
  - dbt runs from its own virtualenv in the image (its pins must not touch Airflow's).
  - The repo `dbt/` is mounted read-only, with target/logs/packages under /tmp.
  - 2 dbt threads in Airflow (4 locally).
- **Memory, re-measured with dbt:** dbt runs inside the scheduler container (LocalExecutor).
  - With 4 threads it hit the old 700 MiB cap (698.7).
  - With 2 threads the peak is scheduler 781 MiB, api-server 209, dag-processor 194, postgres 41: 1.23 GB total.
  - New caps: scheduler 900m, api-server 350m, dag-processor 250m, postgres 150m (1.65 GB). This still fits a t3.small with swap. The scheduler is the one to watch in Phase 5.
- **Deferred to Phase 3/4, by design:** `fct_features_hourly` (built with the leakage test in Phase 3), `fct_forecasts` and `fct_forecast_accuracy` (they need model output; Phase 4 predict/monitor DAGs).
- **Cost so far for Phase 2:** well under USD 0.01 of Athena. Every gold query in the acceptance check scanned < 0.1 MB (criterion: < 10 MB).

## 2026-09-29 — Phase 1 accepted early; OpenAQ CPCB outage investigated
- **Phase 1 accepted at ~18h of the 24h run (owner decision).** Evidence: 47/47 runs succeeded with no retries; no gaps in either hourly DAG (openaq 15:05→09:05, weather 14:10→09:10 UTC); both daily DAGs' first scheduled runs on time (02:00, 03:30 UTC); clean UTC midnight rollover in all four hourly outputs; scheduler memory flat at ~530/700 MiB; 0 task warnings. One unexplained dag-processor self-restart (2026-09-28 16:37 UTC, not OOM, no error logged, recovered in 5 s, no runs affected); watch for repeats. **Not verifiable:** live PM2.5 flowing hourly, because of the outage below. Re-check once OpenAQ recovers.
- **DAGs paused and the Airflow stack stopped** (volumes kept) while Phase 2 is built.
- **The OpenAQ "outage" is OpenAQ's CPCB ingestion, not us:**
  - All 498 CPCB locations in India (plus 46 CPCB-sourced "N/A" ones) stopped at exactly 2026-09-24 17:30 UTC. That's before our key existed (2026-09-28). The key is healthy: HTTP 200, 59/60 remaining, never a 429, and the same key gets fresh AirGradient data in Delhi.
  - AirGradient, Clarity, and AirNow Mumbai/Chennai are current. CPCB's own portal and CAAQMS RSS feed are live (updated 2026-09-29 15:00 IST, includes Anand Vihar).
  - Likely an OpenAQ regression around the fix for openaq/openaq-ingestor#23 (gas ingestion broken 20–25 Sep, fixed 25 Sep). #24 doesn't mention CPCB. No CPCB-specific issue is filed.
- **Correction:** the US Embassy Delhi monitor (AirNow, 8118) stopped at 2026-09-24 **10:30** UTC, 7h before CPCB. It's an unrelated single-station outage. The earlier note that it stopped "at the same moment" compared dates only.
- **Deferred by owner (revisit later):** report the CPCB stop to OpenAQ (the owner files it; Claude can draft). The CPCB-backup idea was later tried and dropped (see Phase 5).
- **Compose project named `checkyouraqi`:** the folder name `airflow` collided with another local project (data-migration-project/airflow) under Docker's default project name, so `docker compose down --remove-orphans` could have removed that project's containers.

## 2026-09-28 — History is 19 months, not 2 years; CAMS added (owner decision)
- **OpenAQ PM2.5 for Delhi NCR stations only exists from ~mid-Feb 2025**, in both the API and the public S3 archive (`openaq-data-archive`, which has 2017, 2018, 2025 and 2026 for Anand Vihar). OpenAQ appears to have lost the CPCB feed between ~2018 and early 2025. Newer stations start later (JNU 2026-02, Wave City 2026-07). So training history is **~19 months with one full stubble/Diwali season (Oct–Nov 2025)**. The Oct 2026 season will come in through live ingestion.
- **Owner chose: use the 19 months, plus CAMS** (Open-Meteo air-quality API, free, history from ~2022-09). Alternatives rejected: OpenAQ only (no regional background signal), or finding older CPCB data elsewhere (manual, licensing unclear, different IDs).
- **CAMS leakage finding:** the air-quality API has no previous-run values (all null), and its history is the model's best estimate per hour, not an archived forecast. So **CAMS history is only used lagged** (value at prediction time − 12h, allowing for CAMS publication delay), never for the target hour. **The CAMS forecast baseline** (plan §7) is scored only on forecasts we collect live with `forecast_issued_at`, starting 2026-09-28. For past periods, "CAMS reanalysis" can be shown as a clearly labelled upper bound, not a fair baseline. Older CAMS history can't add training rows (no observed target before 2025-02).
- **Backfill** (`scripts/backfill.py`, resumable, finished files skipped, the last 7 days always refreshed):
  - OpenAQ PM2.5 from 2025-02, one file per station-month, skipping months before a station's first reading.
  - Weather actuals, previous runs and CAMS from 2025-01, one file per month.
  - FIRMS from 2025-01, per day.
  - The ~1-month head start gives lag features from the first target day. Used the OpenAQ API rather than the S3 archive: same coverage, and ~1,400 calls is ~25 min.
- **`ingest_openaq` is paused during the OpenAQ backfill** so the two don't share the 60/min key limit and risk repeated 429s. Unpause afterwards.
- **Backfill results (2026-09-28):** OpenAQ ~740k station-hours across 69 stations (1,242 station-month files, 41 MB gz). FIRMS 635 days, 158,658 VIIRS detections. Weather, previous runs and CAMS: 21 months. Total bronze ~60 MB. Zero 429s.
- **Wave City (6458520, pm25 sensor 17025422) has no retrievable history:** OpenAQ `/hours` always returns HTTP 500 for it, and `/measurements` returns nothing. It's the same sensor that failed in the Phase 0 spike. Its `/latest` works, so it stays in `stations.yaml` for live data. The backfill now records and skips per-station failures (bad keys still stop the run) instead of crashing.
- **Transport:** a 200 with a non-JSON body (seen once from the Open-Meteo archive) is now retried like a 5xx.

## 2026-09-28 — Ingestion DAGs and bronze format
- **Four DAGs** (Airflow 3.1, LocalExecutor): `ingest_openaq` (hourly :05), `ingest_weather` (hourly :10, forecast), `ingest_weather_actuals` (daily 02:00 UTC, last 7 days), `ingest_fires` (daily 03:30 UTC, last 2 days). All `catchup=False`, `max_active_runs=1`, 2 retries. DAG files only call `ingestion/jobs.py`, which has no Airflow imports, so the jobs are testable and runnable without Airflow.
- **OpenAQ hourly = trailing-6h PM2.5 hourly aggregates + `/latest` per station** (~140 calls/hour, well under 60/min). The overlapping windows catch late readings, and staging dedupes on (sensor_id, hour). `/latest` is kept every hour even when nothing is new, because it feeds "last reading" and the freshness badges. One failing station is recorded in the file's `errors` and doesn't fail the run; a bad key or all stations failing does.
- **Bronze JSON is gzipped** (`.json.gz`, deterministic `mtime=0`). Athena reads gz natively and bills compressed bytes, which matters under the 100 MB per-query cap (the 2-year PM2.5 history is ~700 MB as plain JSON). FIRMS CSVs and station metadata stay uncompressed (small).
- **Airflow 3 gotcha:** manually triggered runs have **no `logical_date`**. The DAGs fall back to `dag_run.run_after`.
- **Custom image** = official `apache/airflow:3.1.0-python3.12` + `pydantic-settings` (installed with `apache-airflow==$AIRFLOW_VERSION` pinned so Airflow's deps can't move). Code and config are mounted read-only. Keys come from the repo `.env`, and `~/.aws` is mounted read-only with `AWS_PROFILE=checkyouraqi-dev` (the EC2 instance role replaces this in Phase 5).
- **Memory under load** (all four DAGs triggered together, 3 tasks concurrent): **peak 981 MiB total** — scheduler 531/700 MiB (it runs the tasks), api-server 316/450, dag-processor 72/250, postgres 63/200. This fits the t3.small budget (~1.6 GB for containers), so the Phase 0 memory spike is closed. The scheduler cap is the one to watch: if it OOMs, lower `PARALLELISM` before resizing.

## 2026-09-28 — NASA FIRMS fires
- **Region:** `fire_bbox` [73.8, 27.5, 78.5, 32.6] in `cities.yaml`, covering Punjab, Haryana and western UP. On 2024-11-01 (stubble peak) there were ~960 VIIRS detections per satellite, vs ~80 on 2026-09-27.
- **Sensors:** VIIRS on Suomi-NPP and NOAA-20. Both have an SP archive back past the 2-year backfill plus NRT. NOAA-21 (no SP archive) and MODIS (coarser) are left out so the feature is consistent across the history.
- **SP vs NRT is chosen per date from FIRMS's live availability table** (on 2026-09-28: SP ≤ 2026-06-30, NRT ≥ 2026-07-01). The client prefers SP. A day can later get both files, so **silver must prefer `_SP` over `_NRT` per day and sensor.**
- **The two satellites see mostly the same fires** (961 vs 955 on 2024-11-01), so **never add their counts together**. In Phase 2, either use one sensor (SNPP, with NOAA-20 as fallback when SNPP is missing) or dedupe detections spatially. Decide when building `int_fires_upwind_daily`.
- **One request per day** (`day_range=1`; the API max is 5) → `bronze/firms/dt=YYYY-MM-DD/<source>.csv`, overwritten on rerun. Empty days still get a header-only file, so "no fires" differs from "not fetched".
- **Key safety:** the key sits in the URL path. It's redacted from all error messages, httpx request logging is off, and the client rejects any 200 response that isn't CSV (FIRMS returns some errors as plain text).

## 2026-09-28 — Open-Meteo weather
- **Leak-free weather for the backfill:** Open-Meteo's previous-runs API returns "the forecast for hour H as issued N days earlier". Verified back to 2024-09, which covers the 2-year backfill, for temperature, RH, wind speed/direction, precipitation and surface pressure. Training features for the 24/48/72h horizons use `<var>_previous_day1/2/3`. Live prediction uses the current forecast, stored with the time we fetched it.
- **Boundary layer height has no previous-run data** (checked GFS, ECMWF and best_match). It's used only as the value observed at prediction time, never as a forecast of the target hour. Archive (ERA5) values in the backfill and forecast-API recent hours live are a small source mismatch, acceptable for v1.
- **`forecast_issued_at` = the time we fetched the forecast**, not the model run time (which Open-Meteo doesn't expose). This is conservative, because the forecast certainly existed by then.
- **The forecast extractor refuses logical hours more than 2h from now.** The forecast API only returns today's forecast, so a backfilled or late run would store it under a past issue time, which is leakage. History comes from previous-runs instead, and the forecast DAG must not catch up.
- **One weather point per zone (9), at the centroid of its non-co-located stations.** Open-Meteo's global models are 10–25 km grids (Anand Vihar snapped ~4 km), so per-station points add little signal at about 8× the API usage (the free tier counts each point as a call).
- **Actuals** are fetched per day from the ERA5 archive, which lags ~5 days. The daily DAG re-fetches the last 7 days so late data fills in (idempotent overwrite).
- **Shared transport (`ingestion/http.py`)** for all API clients: throttling, 429/5xx/transport retries, no retry on 401/403, API error messages surfaced. httpx request logging is off because FIRMS puts its key in the URL.

## 2026-09-28 — Stale data: show the last reading, and say why it's old
- **Owner requirement:** never show a blank when live data is missing. Show each station's last reading with its time (IST), plus an indicator of **why** it's old.
- **Station status** by age of latest reading: live ≤ 3h, delayed ≤ 6h, inactive ≤ 7 days, offline > 7 days. The 3h/6h thresholds match the plan's dbt source-freshness warn/error levels. Offline stations are greyed out and excluded from zone/city medians.
- **Feed status:** ≥ 80% of stations not live at once = **outage**. The message then blames the data service, not the sensor. ≥ 30% = degraded. This separates "sensor inactive" from "service down". On 2026-09-28, 100% of stations are not live, so it's an outage.
- **One source of truth:** thresholds in `config/freshness.yaml`, rules and user-facing wording in `ingestion/freshness.py`. dbt mirrors the thresholds as vars (a test will check they match). Dashboard, API and alerts reuse the Python wording.
- **Alerts** never fire on inactive or offline data. A feed outage sends one "source down / back" notice to the private chat.

## 2026-09-28 — Station discovery
- **Reference-grade = `isMonitor`, OR a name ending in a government agency** (DPCC, CPCB, UPPCB, HSPCB, IMD, IITM, MHUA). Low-cost providers (AirGradient, PurpleAir, Clarity) are always excluded. This recovers 12 government stations that OpenAQ has with `isMonitor=false` and provider "N/A", including two new UPPCB sites (SRM Modinagar since 2026-04, Wave City since 2026-07, so both have short history).
- **Ingest every active reference-grade station (70), not only the ≥70%-coverage ones.** Ingesting is cheap, and low-coverage or outage-hit stations may recover. Coverage is enforced later: dbt gap flags and exclusion from training.
- **Active** means reported within 30 days. **Live sensor** means its latest reading is within 7 days of the station's own last reading. This drops legacy duplicate locations (e.g. Anand Vihar 5509/10487) and dead sensors (e.g. 384).
- **Co-located stations (< 100 m) are flagged** in `stations.yaml` (`colocated_with`) and excluded from zone/city medians. Found: Pusa DPCC 6356 at the same spot as Pusa IMD 5404.
- **Zones:** NCR cities are assigned by a name keyword. Delhi is split by distance from Connaught Place (≤ 6 km = Central) and then by N/E/S/W bearing. **Bahadurgarh** (Haryana) gets its own zone. `zones.yaml` is only written if missing; later runs write `zones.draft.yaml` so hand edits are never overwritten.
- **Bronze:** each run lands the raw `/locations` and `/latest` payloads in `bronze/openaq/locations/dt=YYYY-MM-DD/`.
- ~~The outage is wider than CPCB: the US Embassy monitor also went silent at the same time.~~ _Corrected 2026-09-29: the embassy monitor stopped 7h earlier, unrelated. See the entry above._

## 2026-09-28 — OpenAQ client
- **Pacing:** at least 1.1 s between requests (the limit is ~60/min). The client pauses until the window resets when `x-ratelimit-remaining` ≤ 2. On a 429 it sleeps for `max(x-ratelimit-reset, exponential backoff)`. 5xx and transport errors get exponential backoff, up to 5 tries.
- **401/403 are never retried**, because repeated calls with a bad key risk a ban. Other 4xx errors fail immediately.
- **Pagination stops on a short page**, because `meta.found` can be a string like `">1000"`.
- **API quirks to remember in Phase 2:** OpenAQ hourly buckets are **IST-aligned** (UTC starts at :30), so `int_station_hourly` must build its grid on :30 boundaries or re-bucket explicitly. CPCB raw readings are 15-minute. `/latest` returns dead legacy sensors too (e.g. Anand Vihar's from 2018), so filter by the live sensor ID.
- **Every call returns parsed models plus the raw page JSON**, so extractors validate with pydantic and still land the untouched payload in bronze.

## 2026-09-28 — Phase 1 Terraform
- **Two setups:** `infra/terraform/bootstrap/` (local state) creates the state bucket. The main setup uses the S3 backend with `use_lockfile` (no DynamoDB table). The bucket name lives in a git-ignored `backend.hcl`, and the account ID is looked up at run time, so neither appears in the public repo.
- **State bucket has versioning on** (old versions expire after 90 days) so a bad apply can be rolled back. The data bucket has versioning off, per the plan.
- **Added a third Glue database, `aqi_bronze`**, for dbt source tables over the raw bronze files. The plan listed only silver and gold.
- **`public/` stays private for now.** The scoped public-read policy is added in Phase 4, when the dashboard needs it. Until then the whole bucket is private.
- **Bronze 90-day expiry deferred to Phase 2.** Expiring raw JSON before the Parquet conversion exists would delete the only copy. The rules active now: `athena-results/` expires after 7 days, and incomplete uploads are cleaned up after 7 days.
- **Athena scan cap is 100 MB per query and enforced**, so clients can't override it.
- **One least-privilege policy** (this bucket, the `aqi_*` Glue databases, this workgroup, `/checkyouraqi/*` SSM parameters) is attached to both the `checkyouraqi-dev` user and the Phase 5 EC2 role.
- **Secrets never pass through Terraform state:** the dev user's access key is made by hand in the console, and SSM parameters are created as `CHANGE_ME` placeholders (`ignore_changes = [value]`) and filled with `aws ssm put-parameter`.
- **CLI profiles:** `checkyouraqi` (admin) is used only for Terraform. `checkyouraqi-dev` (least-privilege) is used for pipeline code and Airflow.

## 2026-09-28 — Airflow memory spike (idle)
- **Setup:** Airflow 3.1.0 (`apache/airflow:3.1.0-python3.12`), LocalExecutor, Postgres 16, no Celery/Redis, **no triggerer**, parallelism 4, one API worker. Per-service `mem_limit` caps add up to 1.6 GB: scheduler 700m, api-server 450m, dag-processor 250m, postgres 200m.
- **Why caps instead of shrinking Docker Desktop to 2 GB:** Docker Desktop's memory setting is global and would affect other projects. The caps model the t3.small's budget (2 GB minus about 0.4 GB for Amazon Linux and Docker).
- **Result, idle (6 samples over 2 min):** scheduler ~215 MiB, api-server ~210 MiB, dag-processor ~84–107 MiB, postgres ~73 MiB, **~605 MiB total**. About 1 GB is left for task processes inside the caps, plus a 2–4 GB swap file on EC2.
- **Closed (see "Ingestion DAGs"):** measured under task load, peak 981 MiB.
- ~~Still open: measure under task load.~~ The test DAG wasn't written in Phase 0 (a tooling block). The first real ingestion DAGs in Phase 1 will serve as the load test. If the scheduler cap OOMs, lower parallelism to 2 before resizing the instance.

## 2026-09-28 — Station set for v1
- **Model only reference-grade stations.** Originally this meant `isMonitor = true` (54 pass). _Corrected in "Station discovery" below: 10 government stations lack the flag._ Low-cost AirGradient sensors are left out of v1.
- **CPCB → OpenAQ feed outage seen on 2026-09-24.** Every CPCB station went silent at the same moment, so ingestion has to handle feed-wide gaps, and freshness alerts should say "feed down" rather than flagging 50 stations. Rerun the spike after recovery.
- **Use the live sensor/location ID, not the first match.** OpenAQ keeps dead legacy sensors and duplicate locations for the same physical station.

## 2026-09-28 — AWS account and budget
- **Account:** new account on the credit-based Free plan, IAM admin user `tanz-admin` (root has MFA and isn't used). CLI profile `checkyouraqi`, region `ap-south-1`.
- **Credits:** USD 100, expiring **2027-09-28** (12 months, not the 6 the plan assumed). Up to USD 100 more is available from onboarding tasks.
- **Runway:** at the planned ~USD 20–25/month for EC2 from Phase 5, USD 100 lasts about 4–5 months of compute. **Credits, not the expiry date, will be the limit.** Earn the onboarding credits and revisit the exit plan (§3) about 3 months after Phase 5 goes live.
- **No AWS Budget (owner's decision, 2026-09-28).** The USD 1 budget and zero-spend alert are skipped. The Free plan can't bill past its credits, so the risk is silently using up credits, not a surprise bill. Mitigations: Athena workgroup 100 MB per-query scan cap, `project=checkyouraqi` tags on everything, nothing costly before Phase 5, and a monthly manual check of Billing → Credits. Revisit before Phase 5 (EC2), where credit use jumps to ~USD 20–25/month.

## 2026-09-28 — Scaffold choices
- **Repo root is `CheckYourAQI/`** (not a nested `checkyouraqi/`). The plan moved to `docs/checkyouraqi-plan.md` as §11 lays out.
- **`[tool.uv] package = false`**: uv's editable-install `.pth` file gets the macOS hidden flag on this machine and Python 3.12 skips it. Code runs with `PYTHONPATH=.`; pytest sets `pythonpath`.
- **Delhi NCR bbox `[76.84, 28.35, 77.60, 28.90]`**: covers Delhi, Gurugram, Faridabad, Noida/Greater Noida and Ghaziabad. We'll tighten it after the station spike if it picks up stray stations.
- **`festivals.csv`** holds Diwali only for now (2024 uses 31 Oct, the Delhi observance). Verify the dates and add others (e.g. Dussehra, New Year's Eve) before Phase 3 features.

## Open (Phase 0)
