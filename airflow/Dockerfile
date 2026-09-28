FROM apache/airflow:3.1.0-python3.12
# Only what the base image lacks. Re-pinning apache-airflow stops pip from upgrading any of
# Airflow's own dependencies while resolving ours.
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /requirements.txt
