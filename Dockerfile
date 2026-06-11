# SilentBreak Cloud Run image (root-level so `gcloud run deploy --source .` uses it).
# Defaults to SILENTBREAK_MODE=mock so the hosted URL is fully interactive with
# zero secrets. Real mode is configuration only (set SILENTBREAK_MODE=real plus
# ES_URL/ELASTIC_API_KEY and MCP_URL as env vars on the service).
# Installs requirements-serve.txt only: the ADK/Gemini engine needs the full
# requirements.txt and is not part of this image; the app labels its engine honestly.
FROM python:3.12-slim

WORKDIR /srv/silentbreak

COPY requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

COPY . .

ENV SILENTBREAK_MODE=mock \
    PYTHONUNBUFFERED=1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
