#!/usr/bin/env bash
# Run dbt against Athena with the repo's .env loaded (DATA_BUCKET, AWS_PROFILE).
#   scripts/dbt.sh debug | scripts/dbt.sh build | scripts/dbt.sh run -s stg_openaq__pm25_hours
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
set -a; source "$ROOT/.env"; set +a
export PATH="$HOME/.local/bin:$PATH"
exec dbt "$@" --project-dir "$ROOT/dbt" --profiles-dir "$ROOT/dbt"
