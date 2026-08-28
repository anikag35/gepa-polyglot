FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY gepa_rpc/ ./gepa_rpc/

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && pip install --no-cache-dir . \
 && apt-get purge -y --auto-remove git \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --no-create-home --shell /bin/false appuser && chown appuser /app
USER appuser

EXPOSE 50051

ENTRYPOINT ["gepa-rpc", "--port", "50051"]
