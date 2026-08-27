FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY gepa_rpc/ ./gepa_rpc/

RUN pip install --no-cache-dir .

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

EXPOSE 50051

ENTRYPOINT ["gepa-rpc", "--port", "50051"]
