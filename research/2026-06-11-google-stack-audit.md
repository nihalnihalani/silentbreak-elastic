# Google stack audit — 2026-06-11 (researcher: research-google)

Window: May 12 – Jun 11, 2026. Verdict: **no demo-breakers through the judging window (Jun 22 – Jul 6)**.

| What changed | Source | Action for repo |
|---|---|---|
| gemini-3.5-flash GA (May 19, I/O 2026), no shutdown date | ai.google.dev/gemini-api/docs/deprecations | none — config.py already uses it |
| ADK Python 2.2.0 (Jun 4); 2.0.0 GA May 19 | github.com/google/adk-python/releases | none — requirements pins >=2.2.0,<3 |
| ADK 2.2.0 changed LlmAgent DEFAULT model to gemini-3-flash-preview | adk releases | immune — all 4 agents pass model= explicitly (adk_pipeline.py:244/263/274/283) |
| ADK 2.x renamed turn→step interaction helpers | adk releases | not imported; try/except fallbacks already cover import drift |
| Vertex AI → "Gemini Enterprise Agent Platform" rebrand (Cloud Next 2026) | cloud.google.com Next 2026 wrap-up | branding only; README/Devpost updated with current naming |
| Cloud Run: Vertex integration (Preview), disable run.app URL (Preview), Python 3.14 runtime (Preview) | docs.cloud.google.com/run/docs/release-notes | none — all optional previews |

Name-drops applied to README/Devpost: ADK 2.0 GA (multi-agent + HITL headline features),
Gemini 3.5 Flash GA at I/O 2026, platform rebrand naming.
