# SilentBreak Cloud Run image.
# Defaults to SILENTBREAK_MODE=mock so the hosted URL is fully interactive with
# zero secrets. Real mode is configuration only (set SILENTBREAK_MODE=real plus
# ES_URL/ELASTIC_API_KEY and MCP_URL as env vars on the service).
FROM python:3.12-slim

WORKDIR /srv/silentbreak

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV SILENTBREAK_MODE=mock \
    PYTHONUNBUFFERED=1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
