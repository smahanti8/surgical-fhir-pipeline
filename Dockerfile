# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY src/ src/

FROM python:3.11-slim AS runtime

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /app/src /app/src

# setuptools and wheel ship in the base image, but nothing at runtime needs
# them (the app imports no build tooling). Removing them also removes the
# copies of jaraco.context and wheel vendored inside setuptools that Trivy
# flags (CVE-2026-23949, CVE-2026-24049): fixed upstream, not yet in this base.
RUN pip uninstall -y setuptools wheel

# kpi_store.py writes governance_kpis.db into the working directory by
# default; /app must be owned by appuser or that write fails at import time.
RUN chown -R appuser:appuser /app

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

USER appuser

EXPOSE 8000

# Render sets PORT (default 10000) and requires the service to bind to it;
# fall back to 8000 for local runs. exec keeps uvicorn as PID 1 for signals.
CMD ["sh", "-c", "exec uvicorn surgical_fhir.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
