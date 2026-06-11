# SilentBreak Cloud Run image (root-level so `gcloud run deploy --source .` uses it).
# Installs the FULL requirements.txt (the google-adk / Gemini stack), so the
# real ADK engine exists in the image and the hosted service can run real mode
# end to end. The deterministic engine is the labeled fallback either way.
#
# The image default stays SILENTBREAK_MODE=mock so a bare `docker run` is
# zero-setup and zero-secret; real mode is turned on by the SERVICE env at
# deploy time (SILENTBREAK_MODE=real + ES_URL/ELASTIC_API_KEY + MCP_URL +
# Gemini/Vertex env). The app labels whichever engine actually runs.
FROM python:3.12-slim

WORKDIR /srv/silentbreak

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV SILENTBREAK_MODE=mock \
    PYTHONUNBUFFERED=1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
