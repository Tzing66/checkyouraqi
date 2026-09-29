FROM apache/airflow:3.1.0-python3.12

# LightGBM needs the OpenMP runtime, which the base image lacks.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
USER airflow
# Only what the base image lacks. Re-pinning apache-airflow stops pip from upgrading any of
# Airflow's own dependencies while resolving ours.
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /requirements.txt

# dbt gets its own virtualenv: its pins (agate, protobuf, ...) must never touch Airflow's.
RUN python -m venv /home/airflow/dbt-venv \
    && /home/airflow/dbt-venv/bin/pip install --no-cache-dir "dbt-core==1.12.5" "dbt-athena==1.11.1"
