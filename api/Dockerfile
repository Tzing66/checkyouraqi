# AWS Lambda container image for the API (arm64 / Graviton). Build from the repo root:
#   docker buildx build --platform linux/arm64 --provenance=false -f api/Dockerfile -t checkyouraqi-api .
FROM public.ecr.aws/lambda/python:3.12-arm64

COPY api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Only the code the API imports, plus the config it reads (freshness thresholds, breakpoints).
COPY config/ ${LAMBDA_TASK_ROOT}/config/
COPY ingestion/__init__.py ingestion/settings.py ingestion/freshness.py ${LAMBDA_TASK_ROOT}/ingestion/
COPY ingestion/openaq/__init__.py ingestion/openaq/models.py ingestion/openaq/stations.py ${LAMBDA_TASK_ROOT}/ingestion/openaq/
COPY dashboard/__init__.py dashboard/data.py dashboard/geo.py dashboard/ui.py ${LAMBDA_TASK_ROOT}/dashboard/
COPY api/__init__.py api/app.py api/lambda_handler.py ${LAMBDA_TASK_ROOT}/api/

CMD ["api.lambda_handler.handler"]
