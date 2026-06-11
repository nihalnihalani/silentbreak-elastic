#!/usr/bin/env bash
# SilentBreak real-mode smoke: compose up -> health -> full agent loop -> cleanup.
# Exit 0 + "SMOKE PASS" means seed -> inject -> detect -> flip -> verify -> reverse
# all worked against real Elasticsearch through the official Elastic MCP server.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
STEP="start"
trap 'status=$?; if [ $status -ne 0 ]; then echo "SMOKE FAIL at step: ${STEP} (exit ${status})"; fi' EXIT

STEP="docker compose up"
docker compose up -d

STEP="elasticsearch health"
es_ok=""
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:9200/_cluster/health 2>/dev/null | grep -Eq '"status":"(yellow|green)"'; then
    es_ok=1; break
  fi
  sleep 2
done
[ -n "$es_ok" ] || { echo "elasticsearch did not reach yellow/green within 120s"; exit 1; }
echo "[smoke] elasticsearch healthy"

STEP="mcp ping"
mcp_ok=""
for _ in $(seq 1 30); do
  resp="$(curl -fsS http://localhost:8080/ping 2>/dev/null || true)"
  # The official image answers "Ready" on /ping (older docs said "pong").
  if [ "$resp" = "Ready" ] || [ "$resp" = "pong" ]; then
    mcp_ok=1; break
  fi
  sleep 2
done
[ -n "$mcp_ok" ] || { echo "elastic-mcp did not answer on /ping within 60s"; exit 1; }
echo "[smoke] elastic-mcp healthy (/ping -> ${resp})"

STEP="smoke_real.py"
SILENTBREAK_MODE=real SILENTBREAK_INDEX_PREFIX=sales-smoke- "$PYTHON" scripts/smoke_real.py

STEP="done"
echo "SMOKE PASS"
