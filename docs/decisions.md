# Decision log

Decisions not covered by the plan, newest first. Format: date — decision — why.

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
- **Deferred by owner (revisit later):** (1) report the CPCB stop to OpenAQ (the owner files it; Claude can draft); (2) add CPCB's CAAQMS RSS feed (`airquality.cpcb.gov.in/caaqms/rss_feed`) as a backup live source. It's a snapshot with no history, and its PM values look like 24h min/max/avg rather than hourly concentrations, so verify before use. Station names differ slightly from OpenAQ, so match on coordinates.
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
