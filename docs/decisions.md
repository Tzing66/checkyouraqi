# Decision log

Decisions not covered by the plan, newest first. Format: date — decision — why.

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
- **Still open:** measure under task load. The test DAG wasn't written in Phase 0 (a tooling block). The first real ingestion DAGs in Phase 1 will serve as the load test. If the scheduler cap OOMs, lower parallelism to 2 before resizing the instance.

## 2026-09-28 — Station set for v1
- **Model only reference monitors** (`isMonitor = true`, CPCB network plus the US Embassy). 54 pass the ≥70% rule. The 15 low-cost sensors that also pass are left out of v1.
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
