"""Frozen prompt — Risk / PM Aggregator (flat-ensemble topology).

D1 Step A4. Distinct cache namespace from pipeline-mode `risk_pm`
because (a) the prompt body differs and (b) the input shape differs
(parallel verdicts under `specialist_outputs` rather than a sequential
chain under `upstream_agent_outputs`). The output schema is the same —
`RiskPMAgentOutput` — so flat-mode output is interchangeable with
pipeline-mode output for downstream comparison.

Loads the prompt body from `pm_flat_v0.1.txt` (English plaintext).
"""
from __future__ import annotations

from pathlib import Path

PROMPT_VERSION = "v0.1_2026_04_28"
OUTPUT_SCHEMA_NAME = "RiskPMAgentOutput"

_PROMPT_PATH = Path(__file__).resolve().parent / "pm_flat_v0.1.txt"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

USER_PROMPT_TEMPLATE = """Evidence packet (JSON, pretty-printed; includes specialist_outputs synthesised by the orchestrator — four parallel verdicts):
{{evidence_packet_json}}

Output schema (pydantic .model_json_schema(), pretty-printed):
{{output_schema_json}}

Produce the JSON output now. Output JSON only."""
