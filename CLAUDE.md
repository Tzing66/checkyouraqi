# CheckYourAQI

The source of truth is [docs/checkyouraqi-plan.md](docs/checkyouraqi-plan.md). Read it first, then continue
from the first unchecked item in §12. Tick items off in the plan as they are done.

## Non-negotiables
- **Cost rule (§3):** before anything that touches AWS, say what it costs (or that it's free) and ask first.
  Region is `ap-south-1`. Tag everything `project=checkyouraqi`.
- No secrets in git. Local: `.env`. Cloud: SSM Parameter Store (SecureString).
- All timestamps stored in UTC; convert to IST only at display time.
- Decisions the plan doesn't cover: pick the simplest free option and log it in `docs/decisions.md`.

## Conventions
- Python 3.11+, `uv`, `ruff`, `pytest`, type hints. Small, testable modules; every extractor and transform gets a test.
- The project isn't installed as a package (`[tool.uv] package = false`). Run scripts with
  `PYTHONPATH=. uv run python ...`. Pytest already sets `pythonpath`.
- Docker Desktop doesn't start on its own: `open -a Docker` before `docker compose`.
- Git: commits are authored as Tzing66 (global git config). After a pre-commit hook modifies files,
  re-stage and commit again; never `--amend` a pushed commit.

## Operating the project
- **AWS profiles (account 242254325008 only):** `checkyouraqi` = admin, Terraform only;
  `checkyouraqi-dev` = least-privilege pipeline user (set in `.env`). There is no default profile.
  On EC2 the instance role is used; on GitHub Actions an OIDC role (main branch only).
- **Alerts:** Telegram to the owner's private chat (`alerts/`): any task's final failure (same task muted
  6h) and OpenAQ feed status changes (`feed_alert` in dbt_build). Keys in SSM; state in `ops/alerts/`.
- **CI:** `.github/workflows/ci.yml` runs ruff, pytest, `dbt parse` and `terraform validate` on every push/PR,
  with no AWS access. Keep it green before deploying.
- **Terraform:** `infra/terraform` (state in S3, `backend.hcl` git-ignored). Always
  `terraform plan -out=x.tfplan`, show it, get approval, then `terraform apply x.tfplan`.
  What is switched on (`ec2_enabled`, `api_enabled`, `api_image_tag`) lives in the committed
  `infra/terraform/deployed.auto.tfvars`, never in `-var` flags, so no plan silently destroys it.
- **API:** FastAPI on Lambda (`api/Dockerfile`, `api/lambda_handler.py`). Deploy: commit, run
  `scripts/deploy_api.sh`, set `api_image_tag` in `deployed.auto.tfvars`, plan, apply.
- **dbt:** `scripts/dbt.sh <cmd>` (loads `.env`). Every model has exactly one cadence tag (a test
  enforces it): `--selector hourly | daily | weekly | predict`. Bronze tables:
  `scripts/dbt.sh run-operation create_bronze_tables`. Incremental history readers only read
  recent bronze files, so use `--full-refresh` on them after a manual backfill of older periods.
  Iceberg upkeep: `run-operation iceberg_maintenance` (daily in `dbt_daily`).
- **Airflow DAGs:** ingest_openaq, ingest_weather (hourly); ingest_weather_actuals,
  ingest_fires, dbt_daily, monitor (daily); dbt_build (:25) and predict (:45) hourly.
- **ML:** `python -m ml.train` (deterministic, writes `docs/model_results.md`); `python -m ml.promote`
  (fair champion/challenger on data after production's cutoff); `python -m ml.registry show`.
  Weekly training runs on GitHub Actions (`.github/workflows/train.yml`), not on the server.
- **Serving:** dashboard `PYTHONPATH=. uv run streamlit run dashboard/app.py`; API
  `PYTHONPATH=. uv run uvicorn api.app:app`. Both read only `public/` (S3 or `PUBLIC_BASE_URL`).
- **Costs:** S3 *requests* dominate the S3 bill (not storage). Measure with
  `scripts/s3_request_report.py` (S3 access logs, delivered with hours of delay).
