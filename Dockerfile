FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY gepa_rpc/ ./gepa_rpc/

RUN pip install --no-cache-dir .

EXPOSE 50051

ENTRYPOINT ["gepa-rpc", "--port", "50051"]
