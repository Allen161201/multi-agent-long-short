"""
Baseline-Solo agent (Agent 08) — single-agent ablation baseline.

Step A2 of D1 architecture closure. The baseline_solo agent ingests the
full evidence packet directly and emits a PM-equivalent final decision in
one shot, bypassing the prelude / sleeve / aggregator decomposition. Its
output is benchmarked AGAINST the multi-agent pipeline's Risk/PM output
to measure whether decomposition adds value.

The prompt body is stored as `prompts/baseline_solo_v0.1.txt` (English
plaintext, frozen). This module mirrors the shape of the existing prompt
modules — exports PROMPT_VERSION, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE,
OUTPUT_SCHEMA_NAME — so the runner treats it identically to the six
pipeline agents. The module lives under `src/agents/` per the Step A2
spec; the prompt registry in `prompts/__init__.py` imports it from
there.
"""
from __future__ import annotations

from pathlib import Path

PROMPT_VERSION = "v0.1_2026_04_28"
OUTPUT_SCHEMA_NAME = "RiskPMAgentOutput"

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "baseline_solo_v0.1.txt"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

USER_PROMPT_TEMPLATE = """Evidence packet (JSON, pretty-printed):
{{evidence_packet_json}}

Output schema (pydantic .model_json_schema(), pretty-printed):
{{output_schema_json}}

Produce the JSON output now. Output JSON only."""
