# Decision log

Decisions not covered by the plan, newest first. Format: date — decision — why.

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
- **The outage is wider than CPCB:** the US Embassy (AirNow) monitor also went silent at 2026-09-24 17:30 UTC, so this is an OpenAQ ingestion issue for India.

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
